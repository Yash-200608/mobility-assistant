import time
import cv2
import numpy as np
import mediapipe as mp
from config import Config
from core import global_state, logger
from hardware import HardwareManager
from analytics import FallDetector, GaitAnalyser
from analytics_enhanced import (
    AdaptiveCoachingEngine,
    EnergyExpenditure,
    FallDirectionPredictor,
    GaitFingerprint,
    PhaseDetector,
    PostureMonitor,
    PreFallDetector,
    RehabProgressTracker,
    StrideLengthProxy,
    build_rehab_frame,
)
from audio_fallcontext import AudioFallDetector
from clinical_dashboard import write_session_summary
from io_services import start_io_services
from research_log import log_research_row
from sos import trigger_sos

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
    if w <= 0 or h <= 0: return "far"
    area = max(0, (x2 - x1)) * max(0, (y2 - y1))
    ratio = area / (w * h)
    for t, l in Config.DIST_THRESHOLDS:
        if ratio >= t: return l
    return "far"

def detect_dropoff(frame: np.ndarray) -> bool:
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
gait_fingerprint = GaitFingerprint()
phase_detector = PhaseDetector()
prefall_engine = PreFallDetector()
rehab_tracker = RehabProgressTracker()
audio_fall_detector = AudioFallDetector()
last_slouch_alert_t = 0.0
last_research_log_t = 0.0

def render_safety_hud(frame, sensors, fall_state, current_mode, current_alerts, hardware_alerts: bool):
    global last_us, last_stairs
    t = time.time()
    h, w = frame.shape[:2]

    cv2.putText(frame, f"MODE: {current_mode.upper()}", (10, 30), 0, 0.7, (255, 255, 0), 2)
    alert_color = (0, 255, 0) if current_alerts else (0, 0, 255)
    cv2.putText(frame, f"ALERTS: {'ON' if current_alerts else 'OFF'}", (10, 60), 0, 0.6, alert_color, 2)
    cv2.putText(frame, f"POSTURE: {fall_state}", (10, 90), 0, 0.6, (0, 0, 255) if fall_state in ["FALLING", "FALLEN"] else (0, 255, 0), 2)
    
    run_mode = (Config.SAFETY_RUN_MODE or "software_only").lower().strip()
    if run_mode == "software_only":
        cv2.putText(frame, "SAFETY: CAMERA/MIC", (180, 30), 0, 0.55, (0, 255, 200), 2)
    elif hardware_alerts:
        cv2.putText(frame, "SAFETY: FUSED", (180, 30), 0, 0.55, (0, 255, 100), 2)

    if hardware_alerts:
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

        us_floor = sensors.get("us_floor", 10.0)
        if us_floor != -1.0 and (us_floor > Config.US_FLOOR_DROPOFF_CM or detect_dropoff(frame)):
            cv2.putText(frame, "DROP-OFF/STAIRS DETECTED", (180, 100), 0, 0.7, (0, 0, 255), 2)
            if t - last_stairs > Config.STAIRS_ALERT_INTERVAL:
                global_state.queue_alert("Caution. Drop off or stairs detected.")
                last_stairs = t
    elif run_mode == "fused" and not hardware_alerts:
        cv2.putText(frame, "SENSOR FEED STALE — HW ALERTS OFF", (180, 55), 0, 0.55, (0, 165, 255), 2)

    if fall_state == "FALLEN":
        ov = frame.copy()
        cv2.rectangle(ov, (0,0), (w, h), (0,0,255), -1)
        frame = cv2.addWeighted(ov, 0.3, frame, 0.7, 0)
        cv2.putText(frame, "FALL DETECTED", (w//2-150, h//2), 0, 1.2, (0, 0, 255), 3)
    
    ard_col = (0, 200, 0) if global_state.arduino_connected else (0, 0, 255)
    cv2.putText(frame, f"ARD:{'ON' if global_state.arduino_connected else 'OFF'}", (w-100, 60), 0, 0.5, ard_col, 2)
    return frame

def main():
    global last_nav, last_slouch_alert_t, last_research_log_t
    logger.info("Initializing Medical-Grade AI Mobility Assistant")
    HardwareManager().start()
    start_io_services()
    
    prev_time, fps = time.time(), 0
    
    try:
        while True:
            time.sleep(0.01)
            
            # 1. Thread-safe snapshot of shared state
            frame = global_state.get_frame()
            sensors = global_state.get_sensor_data()
            current_mode = global_state.get_mode()
            current_alerts = global_state.get_alerts_enabled()
            
            # Prevent crashes if camera is still warming up
            if frame is None:
                continue
                
            h, w = frame.shape[:2]

            run_mode = (Config.SAFETY_RUN_MODE or "software_only").lower().strip()
            hardware_alerts = global_state.use_hardware_for_safety()
            prefall_msg = None

            if run_mode == "hardware_strict" and not global_state.sensors_fresh():
                blank = np.zeros((h, w, 3), dtype=np.uint8)
                cv2.putText(blank, "No live hardware feed", (max(10, w // 2 - 280), h // 2 - 20), 0, 0.7, (255, 255, 255), 2)
                cv2.putText(blank, "Connect sensors or set SAFETY_RUN_MODE=software_only", (max(10, w // 2 - 420), h // 2 + 20), 0, 0.55, (200, 200, 200), 2)
                cv2.imshow("AI Mobility Hub", blank)
                if cv2.waitKey(1) == 27:
                    break
                continue

            # 2. Physical SOS (serial) only when fused path trusts hardware
            if hardware_alerts:
                sos_signal = sensors.get("sos", 0)
                if sos_signal == 1:
                    trigger_sos("hardware")

            # 3. IMU fall / tilt only with live hardware (software_only never uses IMU here)
            if hardware_alerts:
                fall_alert = fall_engine.process(sensors)
                if fall_alert:
                    global_state.queue_alert(fall_alert, force=True)
                fall_state = fall_engine.state
                prefall_msg = prefall_engine.process(sensors, fall_state)
                if prefall_msg:
                    global_state.queue_alert(prefall_msg, force=True)
                    rehab_tracker.prefall_alerts += 1
            else:
                fall_state = "CAMERA/MIC"

            frame = render_safety_hud(frame, sensors, fall_state, current_mode, current_alerts, hardware_alerts)

            # 4. Vision & Modes
            if current_mode == "stick":
                global_state.set_rehab_llm_context("mode=stick_obstacle_navigation")
                global_state.set_gait_metrics(None)
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

            else:  # Walker Mode (Computer Vision Gait Analysis)
                if pose:
                    try:
                        res = pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                        if res.pose_landmarks: 
                            mp.solutions.drawing_utils.draw_landmarks(frame, res.pose_landmarks, mp_pose.POSE_CONNECTIONS)
                            
                            # Pass visual landmarks to the new Gait Engine
                            lm = res.pose_landmarks.landmark
                            metrics = gait_engine.process_vision(lm)
                            global_state.set_gait_metrics(metrics)
                            gait_alert = gait_engine.get_alert(metrics)
                            if gait_alert: global_state.queue_alert(gait_alert)

                            sym = metrics["symmetry"]
                            cad = metrics["cadence"]
                            phase_detector.ingest_events(metrics.get("events") or [])
                            gait_fingerprint.update(sym, cad)
                            dev = gait_fingerprint.deviation_score()
                            met = EnergyExpenditure.estimate_met(
                                cad, metrics.get("pattern") == "Active Walking"
                            )
                            slouch = PostureMonitor.slouch_score(lm)
                            stride_val = StrideLengthProxy.estimate(lm)
                            phase_asym = phase_detector.asymmetry_ratio()
                            coaching = AdaptiveCoachingEngine.context_line(dev, met, slouch, phase_asym)
                            global_state.set_rehab_llm_context(coaching)

                            now = time.time()
                            ps = PostureMonitor.alert_if_bad(slouch)
                            if ps and now - last_slouch_alert_t > 15.0:
                                global_state.queue_alert(ps, force=True)
                                last_slouch_alert_t = now
                                rehab_tracker.slouch_alerts += 1

                            if cad > 3:
                                rehab_tracker.tick_walker(sym, cad, dev)

                            direction_hint = ""
                            if hardware_alerts and fall_state in ("FALLING", "FALLEN"):
                                direction_hint = FallDirectionPredictor.predict(
                                    float(sensors.get("pitch", 0.0)),
                                    float(sensors.get("roll", 0.0)),
                                )
                            audio_hit = audio_fall_detector.check_loud_transient(
                                global_state.get_last_mic_rms(), now
                            )
                            prefall_this_frame = prefall_msg if hardware_alerts else None

                            if now - last_research_log_t >= Config.RESEARCH_LOG_INTERVAL_SEC:
                                row = build_rehab_frame(
                                    current_mode,
                                    fall_state,
                                    metrics,
                                    sensors,
                                    gait_fingerprint,
                                    phase_detector,
                                    stride_val,
                                    slouch,
                                    met,
                                    prefall_this_frame is not None,
                                    audio_hit,
                                    direction_hint,
                                )
                                log_research_row(row)
                                last_research_log_t = now

                            # Gait + rehab HUD
                            cv2.putText(frame, f"State: {metrics['pattern']}", (10, h-40), 0, 0.6, (0, 255, 0), 2)
                            sym_col = (0, 255, 0) if sym > 85 else (0, 165, 255) if sym > 70 else (0, 0, 255)
                            cv2.putText(frame, f"Symmetry: {sym}%", (250, h-40), 0, 0.6, sym_col, 2)
                            cad_col = (0, 255, 0) if cad > 60 else (0, 165, 255)
                            cv2.putText(frame, f"Cadence: {cad} spm", (10, h-70), 0, 0.6, cad_col, 2)
                            cv2.putText(
                                frame,
                                f"MET~{met:.1f}  GaitZ:{dev:.1f}  Slouch:{slouch}",
                                (10, h - 100),
                                0,
                                0.5,
                                (200, 220, 200),
                                1,
                            )
                            cv2.putText(
                                frame,
                                f"Stride:{stride_val:.2f}  L/R asym:{phase_asym:.2f}",
                                (10, h - 118),
                                0,
                                0.5,
                                (180, 200, 180),
                                1,
                            )
                            if direction_hint:
                                cv2.putText(
                                    frame,
                                    f"Tilt hint: {direction_hint}",
                                    (10, h - 136),
                                    0,
                                    0.5,
                                    (100, 100, 255),
                                    1,
                                )

                            floor_y = int(h * Config.GAIT_VIRTUAL_FLOOR_Y)
                            cv2.line(frame, (0, floor_y), (w, floor_y), (255, 0, 255), 2)
                            cv2.putText(frame, "Virtual Floor", (10, floor_y - 10), 0, 0.5, (255, 0, 255), 1)
                        else:
                            global_state.set_gait_metrics(None)

                    except Exception as e:
                        logger.warning(f"Pose processing error: {e}")
                else:
                    global_state.set_gait_metrics(None)

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
        try:
            sp = write_session_summary(rehab_tracker.summary())
            logger.info(f"Rehab session summary written: {sp}")
        except Exception as e:
            logger.warning(f"Session summary export failed: {e}")
        time.sleep(0.5)
        cv2.destroyAllWindows()
        if pose: pose.close()

if __name__ == "__main__":
    main()