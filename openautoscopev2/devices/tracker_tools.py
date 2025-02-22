import numpy as np
import os
import onnxruntime
from skimage import filters
import cv2 as cv


IMG_BLUR_SIZE = 5
KERNEL_DILATE = np.ones((13,13))
KERNEL_ERODE = np.ones((7,7))
SMALLEST_TRACKING_OBJECT = 200
SIZE_FLUCTUATIONS = 0.25
CENTER_SPEED = 100
MASK_MEDIAN_BLUR = 9
MASK_KERNEL_BLUR = 5


class Detector():
    
    def __init__(self, tracker, gui_fp):
        self.tracker = tracker
        self.ort_xy10x = onnxruntime.InferenceSession(os.path.join(gui_fp, r'openautoscopev2/models/10x_default_all.onnx'))
    
    def xy4x(self, img):
        ny, nx = img.shape[:2]
        otsu = filters.threshold_otsu(img)
        threshold = 1.2 * otsu if otsu > 50 else 110
        img_mask_objects = img_to_object_mask_threshold(img, threshold=threshold)
        _ys, _xs = np.where(~img_mask_objects)
        _, labels, _, centroids = cv.connectedComponentsWithStats(
            img_mask_objects.astype(np.uint8)
        ) 
        centroids = centroids[:,::-1]
        labels_background = set(labels[_ys, _xs])
        label_values, label_counts = np.unique(labels.flatten(), return_counts=True)
        candidates_info = []
        for label_value, label_count, centroid in zip(label_values, label_counts, centroids):
            if label_value in labels_background:
                continue
            if label_count >= SMALLEST_TRACKING_OBJECT:
                candidates_info.append([
                    labels == label_value,
                    centroid
                ])
        self.tracker.found_trackedworm = False
        img_mask_trackedworm = None
        _d_center_closest = None
        if len(candidates_info) > 0:
            _center_previous = self.tracker.trackedworm_center \
                if self.tracker.tracking and self.tracker.trackedworm_center is not None else np.array([ny/2, nx/2])
            _size_lower = self.tracker.trackedworm_size*(1.0-SIZE_FLUCTUATIONS) if self.tracker.tracking and self.tracker.trackedworm_size is not None else 0.0
            _size_upper = self.tracker.trackedworm_size*(1.0+SIZE_FLUCTUATIONS) if self.tracker.tracking and self.tracker.trackedworm_size is not None else 0.0
            for _, (mask, center) in enumerate(candidates_info):
                _size = mask.sum()
                _d_center = np.max(np.abs(center - _center_previous))
                is_close_enough = _d_center <= CENTER_SPEED
                if _size_upper != 0.0:
                    is_close_enough = is_close_enough and (_size_lower <= _size <= _size_upper)
                if is_close_enough:
                    self.tracker.found_trackedworm = True
                    if _d_center_closest is None or _d_center < _d_center_closest:
                        _d_center_closest = _d_center
                        img_mask_trackedworm = mask
                        if self.tracker.tracking:
                                self.tracker.trackedworm_size = _size
                                self.tracker.trackedworm_center = center.copy()

        if self.tracker.found_trackedworm:
            img_mask_trackedworm_blurred = cv.blur(
                img_mask_trackedworm.astype(np.float32),
                (MASK_KERNEL_BLUR, MASK_KERNEL_BLUR)
            ) > 1e-4
            ys, xs = np.where(img_mask_trackedworm_blurred)
            y_min, y_max = minmax(ys)
            x_min, x_max = minmax(xs)
            self.tracker.x_worm = (x_min + x_max)//2
            self.tracker.y_worm = (y_min + y_max)//2

            img_annotated = cv.rectangle(img_annotated, (x_min, y_min), (x_max, y_max), 255, 2)
            img_annotated = cv.circle(img_annotated, (255, 255), radius=2, color=255, thickness=2)
        
        return img_annotated

    def xy10x(self, img):
        self.found_trackedworm = True
        img_cropped = img[56:-56,56:-56]
        batch_1_400_400 = {
            'input': np.repeat(
                img_cropped[None, None, :, :], 3, 1
            ).astype(np.float32)
        }
        ort_outs = self.ort_xy10x.run( None, batch_1_400_400 )
        # The network is trained to output (x, y)
        self.x_worm, self.y_worm = ort_outs[0][0].astype(np.int64) + 56

        img_annotated = img.copy()
        img_annotated = cv.circle(img_annotated, (int(self.x_worm), int(self.y_worm)), radius=10, color=255, thickness=2)
        img_annotated = cv.circle(img_annotated, (255, 255), radius=2, color=255, thickness=2)

        return img_annotated

def minmax(arr):
    return np.min(arr), np.max(arr)

def img_to_object_mask_threshold(img, threshold):
    img_blurred = cv.blur(img, (IMG_BLUR_SIZE, IMG_BLUR_SIZE))
    img_objects = (img_blurred < threshold).astype(np.float32)
    img_objects_eroded = cv.erode(img_objects, KERNEL_ERODE).astype(np.float32)
    img_objects_dilated = cv.dilate(img_objects_eroded, KERNEL_DILATE).astype(np.float32)
    _, labels, rectangles = cv.connectedComponentWithStats(img_objects_dilated)
    for i, rectangle in enumerate(rectangles):
        _size = rectangle[-1]
        if _size <= SMALLEST_TRACKING_OBJECT:
            indices = labels == i
            labels[indices] = 0
    mask = labels > 0
    return mask