import sys
import time
import cv2
import numpy as np
import mediapipe as mp
from config import Config
from core import global_state, logger
from hardware import HardwareManager
from analytics import FallDetector, GaitAnalyser
from io_services import start_io_services

try: mp_pose = mp.solutions.pose; pose = mp_pose.Pose(min_detection_confidence=0.6, min_tracking_confidence=0.6)
except Exception: pose = None

class ObjectTracker:
    def __init__(self):
        self.tracks, self.next_id = {}, 0
    def update(self, detections, w, h):
        now = time.time()
        for tid in [tid for tid, t in self.tracks.items() if now - t["last_seen"] > Config.TRACKED_TTL]:
            del self.tracks[tid]
        
        updated = []
        unmatched = list(detections)
        for tid, track in list(self.tracks.items()):
            best_i, best_iou = -1, 0.0
            for i, det in enumerate(unmatched):
                if det["class"] != track["label"]: continue
                box1 = (track["x1"], track["y1"], track["x2"], track["y2"])
                box2 = (det["x"]-det["width"]/2, det["y"]-det["height"]/2, det["x"]+det["width"]/2, det["y"]+det["height"]/2)
                x1, y1 = max(box1[0], box2[0]), max(box1[1], box2[1])
                x2, y2 = min(box1[2], box2[2]), min(box1[3], box2[3])
                inter = max(0, x2-x1) * max(0, y2-y1)
                union = (box1[2]-box1[0])*(box1[3]-box1[1]) + (box2[2]-box2[0])*(box2[3]-box2[1]) - inter
                iou = inter / union if union > 0 else 0
                if iou > best_iou: best_i, best_iou = i, iou
            
            if best_iou > Config.IOU_MATCH_THRESH:
                m = unmatched.pop(best_i)
                track.update({"x1": m["x"]-m["width"]/2, "y1": m["y"]-m["height"]/2, "x2": m["x"]+m["width"]/2, "y2": m["y"]+m["height"]/2, "last_seen": now})
                updated.append(track)
                
        for m in unmatched:
            new_t = {"label": m["class"], "x1": m["x"]-m["width"]/2, "y1": m["y"]-m["height"]/2, "x2": m["x"]+m["width"]/2, "y2": m["y"]+m["height"]/2, "last_seen": now, "last_alert": 0, "id": self.next_id}
            self.tracks[self.next_id] = new_t
            updated.append(new_t)
            self.next_id += 1
        return updated

def estimate_dist(x1, y1, x2, y2, w, h):
    """Estimates distance with guards against zero-area and negative-area boxes."""
    if w <= 0 or h <= 0:
        return "far"
    area = max(0, (x2 - x1)) * max(0, (y2 - y1))
    ratio = area / (w * h)
    for t, l in Config.DIST_THRESHOLDS:
        if ratio >= t: return l
    return "far"

def detect_dropoff(frame: np.ndarray) -> bool:
    """Detects floor drop-off or stairs using Canny edge density on the bottom 35%."""
    try:
        h, w = frame.shape[:2]
        roi = frame[int(h * 0.65):h, 0:w]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)
        density = np.sum(edges > 0) / (edges.shape[0] * edges.shape[1])
        return density < 0.02
    except Exception as e:
        logger.warning(f"Dropoff detection failed: {e}")
        return False

last_nav, last_stairs, last_us = 0.0, 0.0, 0.0
tracker = ObjectTracker()
fall_engine = FallDetector()
gait_engine = GaitAnalyser()

def render_safety_hud(frame, sensors, fall_state, current_mode, current_alerts):
    """Renders safety HUD using pre-fetched thread-safe state snapshots."""
    global last_us, last_stairs
    t = time.time()
    h, w = frame.shape[:2]

    cv2.putText(frame, f"MODE: {current_mode.upper()}", (10, 30), 0, 0.7, (255, 255, 0), 2)
    alert_color = (0, 255, 0) if current_alerts else (0, 0, 255)
    cv2.putText(frame, f"ALERTS: {'ON' if current_alerts else 'OFF'}", (10, 60), 0, 0.6, alert_color, 2)
    cv2.putText(frame, f"POSTURE: {fall_state}", (10, 90), 0, 0.6, (0, 0, 255) if fall_state in ["FALLING", "FALLEN"] else (0, 255, 0), 2)
    
    # Ultrasonic obstacle alert with Hardware Fault Tolerance
    us_f = sensors.get("us_front", 999.0)
    if us_f == -1.0:
        cv2.putText(frame, "FRONT SENSOR FAULT", (180, 55), 0, 0.7, (0, 165, 255), 2)
    elif us_f < Config.US_FRONT_STOP_CM:
        cv2.putText(frame, "STOP! Obstacle", (180, 55), 0, 0.7, (0, 0, 255), 2)
        if t - last_us > Config.ULTRASONIC_ALERT_INTERVAL:
            global_state.queue_alert("Stop! Obstacle immediately ahead.", force=True)
            last_us = t
    elif us_f < Config.US_FRONT_WARN_CM:
        cv2.putText(frame, f"Obstacle: {int(us_f)}cm", (180, 55), 0, 0.7, (0, 165, 255), 2)

    # Drop-off / stairs detection
    us_floor = sensors.get("us_floor", 10.0)
    if us_floor != -1.0 and (us_floor > Config.US_FLOOR_DROPOFF_CM or detect_dropoff(frame)):
        cv2.putText(frame, "DROP-OFF/STAIRS DETECTED", (180, 100), 0, 0.7, (0, 0, 255), 2)
        if t - last_stairs > Config.STAIRS_ALERT_INTERVAL:
            global_state.queue_alert("Caution. Drop off or stairs detected.")
            last_stairs = t

    # Fall overlay
    if fall_state == "FALLEN":
        ov = frame.copy()
        cv2.rectangle(ov, (0,0), (w, h), (0,0,255), -1)
        frame = cv2.addWeighted(ov, 0.3, frame, 0.7, 0)
        cv2.putText(frame, "FALL DETECTED", (w//2-150, h//2), 0, 1.2, (0, 0, 255), 3)
    
    # Arduino status
    ard_col = (0, 200, 0) if global_state.arduino_connected else (0, 0, 255)
    cv2.putText(frame, f"ARD:{'ON' if global_state.arduino_connected else 'OFF'}", (w-100, 60), 0, 0.5, ard_col, 2)
    return frame

def main():
    logger.info("Initializing Medical-Grade AI Mobility Assistant")
    HardwareManager().start()
    start_io_services()
    
    prev_time, fps = time.time(), 0
    global last_nav
    
    try:
        while True:
            time.sleep(0.01)
            frame = global_state.get_frame()
            if frame is None:
                continue
            
            # Thread-safe snapshot of shared state
            current_mode = global_state.get_mode()
            current_alerts = global_state.get_alerts_enabled()
            sensors = global_state.get_sensor_data()
            h, w = frame.shape[:2]

            # Critical Safety Engine
            fall_alert = fall_engine.process(sensors)
            if fall_alert: global_state.queue_alert(fall_alert, force=True)
            frame = render_safety_hud(frame, sensors, fall_engine.state, current_mode, current_alerts)

            # Vision & Modes
            if current_mode == "stick":
                dets = global_state.get_detections()
                nav_objs = []
                for trk in tracker.update(dets, w, h):
                    x1, y1, x2, y2 = int(trk["x1"]), int(trk["y1"]), int(trk["x2"]), int(trk["y2"])
                    dist = estimate_dist(x1, y1, x2, y2, w, h)
                    cv2.rectangle(frame, (x1,y1), (x2,y2), (0,255,0), 2)
                    cv2.putText(frame, f"{trk['label']} {dist}", (x1, max(20, y1-5)), 0, 0.5, (255, 0, 0), 1)
                    if dist in ["very close", "close"]: nav_objs.append((x1, x2))
                    
                    if dist == "very close" and time.time() - trk["last_alert"] > 3.0:
                        trk["last_alert"] = time.time()
                        global_state.queue_alert(f"{trk['label']} very close.")

                if time.time() - last_nav > Config.NAV_ALERT_INTERVAL and nav_objs:
                    centers = [(a+b)/2 for a,b in nav_objs]
                    left_obs = sum(1 for c in centers if c < w * 0.4)
                    right_obs = sum(1 for c in centers if c > w * 0.6)
                    mid_obs = sum(1 for c in centers if w*0.4 <= c <= w*0.6)
                    if mid_obs > 0:
                        if left_obs > right_obs: global_state.queue_alert("Move slightly right.")
                        elif right_obs > left_obs: global_state.queue_alert("Move slightly left.")
                        else: global_state.queue_alert("Stop. Obstacle ahead.")
                    elif left_obs > 0 and right_obs == 0: global_state.queue_alert("Move right.")
                    elif right_obs > 0 and left_obs == 0: global_state.queue_alert("Move left.")
                    last_nav = time.time()

            else:  # Walker Mode
                if pose:
                    try:
                        res = pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                        if res.pose_landmarks: mp.solutions.drawing_utils.draw_landmarks(frame, res.pose_landmarks, mp_pose.POSE_CONNECTIONS)
                    except Exception as e:
                        logger.warning(f"Pose processing error: {e}")
                
                metrics = gait_engine.process(sensors)
                gait_alert = gait_engine.get_alert(metrics)
                if gait_alert: global_state.queue_alert(gait_alert)
                
                # Gait HUD
                pat_col = (0, 255, 0) if metrics["confidence"] > 0.85 else (0, 200, 255)
                cv2.putText(frame, f"Gait: {metrics['pattern']} ({metrics['confidence']*100:.0f}%)", (10, h-40), 0, 0.6, pat_col, 2)
                sym = metrics["symmetry"] or 0
                sym_col = (0, 255, 0) if sym > 85 else (0, 165, 255) if sym > 70 else (0, 0, 255)
                cv2.putText(frame, f"Symmetry: {sym}%", (250, h-40), 0, 0.6, sym_col, 2)
                cad_col = (0, 255, 0) if (metrics["cadence"] or 0) > 60 else (0, 165, 255)
                cv2.putText(frame, f"Cadence: {metrics['cadence'] or 0} spm", (10, h-70), 0, 0.6, cad_col, 2)
                if metrics["affected"]:
                    cv2.putText(frame, f"Affected: {metrics['affected']}", (250, h-70), 0, 0.6, (0, 200, 255), 2)

            # FPS overlay
            curr = time.time()
            if curr - prev_time > 0.01: fps = 0.9*fps + 0.1*(1/(curr-prev_time))
            prev_time = curr
            cv2.putText(frame, f"FPS: {int(fps)}", (w-80, 30), 0, 0.7, (200, 200, 200), 2)
            cv2.imshow("AI Mobility Hub", frame)

            if cv2.waitKey(1) == 27: break

    except KeyboardInterrupt: pass
    finally:
        logger.info("Executing safe teardown procedures...")
        global_state.is_shutting_down = True
        time.sleep(0.5)  
        cv2.destroyAllWindows()
        if pose: pose.close()

if __name__ == "__main__":
    main()