import cv2
import numpy as np

img = cv2.imread("e:/考研/408/rendered_pages/page_015.jpg")
hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

# Yellow highlighter: usually H is around 20-40, S is high, V is high.
# Red pen: usually H is around 0-10 or 170-180, S is high, V is medium-high.
# Let's count pixels matching these ranges.

# Yellow range
lower_yellow = np.array([20, 50, 50])
upper_yellow = np.array([40, 255, 255])
yellow_mask = cv2.inRange(hsv, lower_yellow, upper_yellow)
yellow_pixels = cv2.countNonZero(yellow_mask)

# Red range
lower_red1 = np.array([0, 50, 50])
upper_red1 = np.array([10, 255, 255])
lower_red2 = np.array([170, 50, 50])
upper_red2 = np.array([180, 255, 255])
red_mask = cv2.bitwise_or(cv2.inRange(hsv, lower_red1, upper_red1), cv2.inRange(hsv, lower_red2, upper_red2))
red_pixels = cv2.countNonZero(red_mask)

print(f"Page 15: Yellow pixels={yellow_pixels}, Red pixels={red_pixels}")

# Let's also check other pages
for p in range(16, 21):
    path = f"e:/考研/408/rendered_pages/page_{p:03d}.jpg"
    im = cv2.imread(path)
    if im is None:
        continue
    h = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    y_mask = cv2.inRange(h, lower_yellow, upper_yellow)
    r_mask = cv2.bitwise_or(cv2.inRange(h, lower_red1, upper_red1), cv2.inRange(h, lower_red2, upper_red2))
    print(f"Page {p}: Yellow pixels={cv2.countNonZero(y_mask)}, Red pixels={cv2.countNonZero(r_mask)}")
