import cv2
import numpy as np
import pytesseract
from PIL import ImageGrab

class ScreenReader:
    def __init__(self, region=None):
        self.region = region
        
    def capture(self):
        """Chụp màn hình với vùng chỉ định"""
        if self.region:
            screen = np.array(ImageGrab.grab(bbox=self.region))
        else:
            screen = np.array(ImageGrab.grab())
        return cv2.cvtColor(screen, cv2.COLOR_BGR2RGB)
    
    def extract_text(self, image):
        """Trích xuất văn bản từ ảnh"""
        return pytesseract.image_to_string(
            image, 
            config='--psm 6 --oem 3 -l eng'
        )