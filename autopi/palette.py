"""AutoPi's colors - one place, so every screen matches.

Nov 2026 color scheme (driver's choice): NO YELLOW TEXT. Faces, face labels,
the face screen, gauges and graph lines keep their original colors.
    GREEN   raw data read from the car
    PURPLE  AutoPi's explanation / what to do / attention (was yellow)
    BLUE    the driver's own words (TALK TO AUTOPI)
    RED     faults / danger
"""

GREEN = (57, 255, 120)
PURPLE = (175, 125, 255)          # AutoPi says / attention (replaces yellow)
PURPLE_DIM = (125, 100, 170)      # secondary purple text
BLUE = (90, 170, 255)             # what the driver said
RED = (255, 60, 60)
YELLOW = (255, 215, 40)           # faces / face screen / gauges / graph lines ONLY - never plain text

# Same colors for the HTML report (screen + print versions).
HTML_PURPLE = "#af7dff"
HTML_PURPLE_PRINT = "#5a2ea0"     # darker purple that prints readably on white
