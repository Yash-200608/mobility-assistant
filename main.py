import time
import cv2
import numpy as np
import mediapipe as mp
from config import Config
from core import global_state, logger
from hardware import HardwareManager
from analytics_enhanced import RehabProgressTracker
from clinical_dashboard import write_session_summary
from escalation import fall_escalator
from io_services import start_io_services
from navigation import NavigationAdvisor, ObjectTracker, estimate_dist  # noqa: F401  (re-exported)
from pipeline import HardwareSafety, WalkerAnalytics
from research_log import log_research_row

try: mp_pose = mp.solutions.pose; pose = mp_pose.Pose(min_detection_confidence=0.6, min_tracking_confidence=0.6)
except Exception: pose = None

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

last_us, last_stairs = 0.0, 0.0

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
        if fall_escalator.pending:
            cv2.putText(frame, "Say 'I'm okay' to cancel SOS", (w//2-200, h//2+40), 0, 0.7, (255, 255, 255), 2)

    ard_col = (0, 200, 0) if global_state.arduino_connected else (0, 0, 255)
    cv2.putText(frame, f"ARD:{'ON' if global_state.arduino_connected else 'OFF'}", (w-100, 60), 0, 0.5, ard_col, 2)
    return frame

def draw_walker_hud(frame, wf, floor_ratio):
    h, w = frame.shape[:2]
    sym_col = (0, 255, 0) if wf.symmetry > 85 else (0, 165, 255) if wf.symmetry > 70 else (0, 0, 255)
    cad_col = (0, 255, 0) if wf.cadence > 60 else (0, 165, 255)
    cv2.putText(frame, f"State: {wf.metrics['pattern']}", (10, h-40), 0, 0.6, (0, 255, 0), 2)
    cv2.putText(frame, f"Symmetry: {wf.symmetry}%", (250, h-40), 0, 0.6, sym_col, 2)
    cv2.putText(frame, f"Cadence: {wf.cadence} spm", (10, h-70), 0, 0.6, cad_col, 2)
    cv2.putText(frame, f"MET~{wf.met:.1f}  GaitZ:{wf.deviation:.1f}  Slouch:{wf.slouch}",
                (10, h - 100), 0, 0.5, (200, 220, 200), 1)
    cv2.putText(frame, f"Stride:{wf.stride:.2f}  L/R asym:{wf.phase_asym:.2f}",
                (10, h - 118), 0, 0.5, (180, 200, 180), 1)
    if wf.direction_hint:
        cv2.putText(frame, f"Tilt hint: {wf.direction_hint}", (10, h - 136), 0, 0.5, (100, 100, 255), 1)
    floor_y = int(h * floor_ratio)
    cv2.line(frame, (0, floor_y), (w, floor_y), (255, 0, 255), 2)
    cv2.putText(frame, "Virtual Floor", (10, floor_y - 10), 0, 0.5, (255, 0, 255), 1)

def main():
    logger.info("Initializing AI Mobility Assistant (research prototype, not a medical device)")
    hw = HardwareManager()
    hw.start()
    start_io_services()

    rehab_tracker = RehabProgressTracker()
    safety = HardwareSafety(buzz_fn=hw.buzz)
    walker = WalkerAnalytics(rehab=rehab_tracker, log_fn=log_research_row)
    navigator = NavigationAdvisor()

    prev_time, fps = time.time(), 0

    try:
        while True:
            time.sleep(0.01)

            frame = global_state.get_frame()
            sensors = global_state.get_sensor_data()
            current_mode = global_state.get_mode()
            current_alerts = global_state.get_alerts_enabled()

            if frame is None:
                continue

            h, w = frame.shape[:2]
            now = time.time()

            run_mode = (Config.SAFETY_RUN_MODE or "software_only").lower().strip()
            hardware_alerts = global_state.use_hardware_for_safety()
            prefall_fired = False

            if run_mode == "hardware_strict" and not global_state.sensors_fresh():
                safety.idle_tick(now)
                blank = np.zeros((h, w, 3), dtype=np.uint8)
                cv2.putText(blank, "No live hardware feed", (max(10, w // 2 - 280), h // 2 - 20), 0, 0.7, (255, 255, 255), 2)
                cv2.putText(blank, "Connect sensors or set SAFETY_RUN_MODE=software_only", (max(10, w // 2 - 420), h // 2 + 20), 0, 0.55, (200, 200, 200), 2)
                cv2.imshow("AI Mobility Hub", blank)
                if cv2.waitKey(1) == 27:
                    break
                continue

            if hardware_alerts:
                result = safety.step(sensors, now)
                fall_state = result.fall_state
                prefall_fired = result.prefall_alert is not None
                if prefall_fired:
                    rehab_tracker.prefall_alerts += 1
            else:
                safety.idle_tick(now)
                fall_state = "CAMERA/MIC"

            frame = render_safety_hud(frame, sensors, fall_state, current_mode, current_alerts, hardware_alerts)

            if current_mode == "stick":
                global_state.set_rehab_llm_context("mode=stick_obstacle_navigation")
                global_state.set_gait_metrics(None)
                for box in navigator.step(global_state.get_detections(), w, h, now):
                    cv2.rectangle(frame, (box["x1"], box["y1"]), (box["x2"], box["y2"]), (0, 255, 0), 2)
                    cv2.putText(frame, f"{box['label']} {box['dist']}", (box["x1"], max(20, box["y1"] - 5)),
                                0, 0.5, (255, 0, 0), 1)
            else:
                if pose:
                    try:
                        res = pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                        if res.pose_landmarks:
                            mp.solutions.drawing_utils.draw_landmarks(frame, res.pose_landmarks, mp_pose.POSE_CONNECTIONS)
                            wf = walker.step(
                                res.pose_landmarks.landmark, current_mode, fall_state, sensors,
                                hardware_alerts, prefall_fired=prefall_fired, now=now,
                            )
                            draw_walker_hud(frame, wf, Config.GAIT_VIRTUAL_FLOOR_Y)
                        else:
                            global_state.set_gait_metrics(None)
                    except Exception as e:
                        logger.warning(f"Pose processing error: {e}")
                else:
                    global_state.set_gait_metrics(None)

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
