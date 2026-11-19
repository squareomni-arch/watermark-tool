import pyautogui
import time
import math

class GestureDetector:
    def __init__(self, gesture_config):
        self.gestures = gesture_config
        self.scroll_count = 0
        self.last_scroll_time = 0
        self.click_count = 0
        self.last_click_time = 0
    
    def check_gestures(self):
        """Kiểm tra cử chỉ kích hoạt"""
        for gesture in self.gestures:
            if gesture["type"] == "scroll":
                if not self._check_scroll_gesture(gesture["direction"], gesture["count"]):
                    return False
            elif gesture["type"] == "click":
                if not self._check_click_gesture(gesture["button"], gesture["corner"]):
                    return False
        return True
    
    def _check_scroll_gesture(self, direction, count):
        """Kiểm tra cử chỉ cuộn"""
        current_time = time.time()
        if current_time - self.last_scroll_time > 1.0:  # Reset nếu quá 1 giây không cuộn
            self.scroll_count = 0
        
        # Giả lập sự kiện cuộn (thực tế cần kết nối với sự kiện hệ thống)
        scroll_event = {"direction": "up" if pyautogui.position().y < 100 else "down"}
        
        if scroll_event["direction"] == direction:
            self.scroll_count += 1
            self.last_scroll_time = current_time
            if self.scroll_count >= count:
                self.scroll_count = 0
                return True
        return False
    
    def _check_click_gesture(self, button, corner):
        """Kiểm tra click chuột ở góc"""
        current_time = time.time()
        if current_time - self.last_click_time > 1.0:  # Reset nếu quá 1 giây không click
            self.click_count = 0
        
        # Giả lập sự kiện click (thực tế cần kết nối với sự kiện hệ thống)
        click_pos = pyautogui.position()
        screen_width, screen_height = pyautogui.size()
        
        if corner == "bottom_left":
            in_corner = (click_pos.x < 100 and click_pos.y > screen_height - 100)
        else:
            in_corner = False
        
        if in_corner and pyautogui.mouseDown(button=button):
            self.click_count += 1
            self.last_click_time = current_time
            if self.click_count >= 2:  # Yêu cầu 2 click
                self.click_count = 0
                return True
        return False