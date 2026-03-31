import numpy as np
import cv2

class KalmanStabilityTracker:
    """Uses a Kalman filter to smooth bounding box coordinates (x, y, w, h)."""
    def __init__(self):
        # State: [x, y, w, h, vx, vy, vw, vh] (position + velocity)
        self.kf = cv2.KalmanFilter(8, 4)
        
        # Measurement matrix: we measure x, y, w, h directly
        self.kf.measurementMatrix = np.array(
            [[1, 0, 0, 0, 0, 0, 0, 0],
             [0, 1, 0, 0, 0, 0, 0, 0],
             [0, 0, 1, 0, 0, 0, 0, 0],
             [0, 0, 0, 1, 0, 0, 0, 0]], np.float32)
        
        # Transition matrix: simple constant velocity model
        self.kf.transitionMatrix = np.array(
            [[1, 0, 0, 0, 1, 0, 0, 0],
             [0, 1, 0, 0, 0, 1, 0, 0],
             [0, 0, 1, 0, 0, 0, 1, 0],
             [0, 0, 0, 1, 0, 0, 0, 1],
             [0, 0, 0, 0, 1, 0, 0, 0],
             [0, 0, 0, 0, 0, 1, 0, 0],
             [0, 0, 0, 0, 0, 0, 1, 0],
             [0, 0, 0, 0, 0, 0, 0, 1]], np.float32)
        
        # Process noise and measurement noise (tuned for smooth tracking)
        self.kf.processNoiseCov = np.eye(8, dtype=np.float32) * 0.03
        self.kf.measurementNoiseCov = np.eye(4, dtype=np.float32) * 0.1
        
        self.initialized = False

    def update(self, detected_box):
        """
        Update with detected bounding box and return smoothed box.
        Args:
            detected_box: (x, y, w, h)
        Returns:
            smoothed_box: (x, y, w, h) after Kalman filtering
        """
        x, y, w, h = detected_box
        measurement = np.array([[x], [y], [w], [h]], dtype=np.float32)
        
        if not self.initialized:
            # First detection: initialize filter state
            self.kf.statePost = np.array([x, y, w, h, 0, 0, 0, 0], dtype=np.float32)
            self.initialized = True
            return (x, y, w, h)
        
        # Predict next state
        prediction = self.kf.predict()
        
        # Correct with measurement
        self.kf.correct(measurement)
        
        # Extract smoothed coordinates from state
        state = self.kf.statePost
        smoothed_x = int(state[0])
        smoothed_y = int(state[1])
        smoothed_w = int(state[2])
        smoothed_h = int(state[3])
        
        return (smoothed_x, smoothed_y, smoothed_w, smoothed_h)

    def reset(self):
        """Reset the filter."""
        self.initialized = False    
    def is_stable(self, velocity_threshold=1.5):
        """
        Check if the face position is stable (low velocity).
        Args:
            velocity_threshold: Maximum velocity magnitude to be considered stable
        Returns:
            True if the face is stable, False otherwise
        """
        if not self.initialized:
            return False
        
        state = self.kf.statePost
        vx = state[4]  # velocity in x
        vy = state[5]  # velocity in y
        velocity_magnitude = np.sqrt(vx**2 + vy**2)
        
        return velocity_magnitude < velocity_threshold