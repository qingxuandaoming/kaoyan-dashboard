import fitz
import os
import cv2
import numpy as np
from PIL import Image

def scan_pdf_for_color_annotations(pdf_path, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    total_pages = doc.page_count
    
    print(f"Total pages in PDF: {total_pages}")
    
    # Define color ranges in HSV
    # Yellow highlighter
    lower_yellow = np.array([20, 50, 80])
    upper_yellow = np.array([40, 255, 255])
    
    # Red pen / mark
    lower_red1 = np.array([0, 50, 50])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([170, 50, 50])
    upper_red2 = np.array([180, 255, 255])
    
    # Blue / Green highlighter
    lower_green = np.array([35, 40, 50])
    upper_green = np.array([85, 255, 255])
    
    candidates = []
    
    for i in range(total_pages):
        page = doc[i]
        # Render page at 150 DPI
        pix = page.get_pixmap(dpi=150)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        
        # Convert PIL image to OpenCV format (BGR)
        img_np = np.array(img)
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        
        # Convert to HSV
        hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
        
        # Count yellow pixels
        yellow_mask = cv2.inRange(hsv, lower_yellow, upper_yellow)
        yellow_pixels = cv2.countNonZero(yellow_mask)
        
        # Count red pixels
        red_mask = cv2.bitwise_or(cv2.inRange(hsv, lower_red1, upper_red1), cv2.inRange(hsv, lower_red2, upper_red2))
        red_pixels = cv2.countNonZero(red_mask)
        
        # Count green/blue pixels
        green_mask = cv2.inRange(hsv, lower_green, upper_green)
        green_pixels = cv2.countNonZero(green_mask)
        
        # Print info for debug
        if yellow_pixels > 1000 or red_pixels > 500 or green_pixels > 1000:
            candidates.append({
                "page_idx": i,
                "page_num": i + 1,
                "yellow": yellow_pixels,
                "red": red_pixels,
                "green": green_pixels
            })
            print(f"Page {i+1}: Yellow={yellow_pixels}, Red={red_pixels}, Green={green_pixels}")
            
    print(f"Found {len(candidates)} candidate pages with potential color annotations.")
    return candidates

if __name__ == "__main__":
    pdf_path = "e:/考研/408/2027数据结构_高清带书签版.pdf"
    output_dir = "e:/考研/408/temp_scan"
    scan_pdf_for_color_annotations(pdf_path, output_dir)
