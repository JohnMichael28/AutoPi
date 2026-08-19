from enum import Enum


class AppState(Enum):
    """The device's states, as a simple built-in Enum (no library - lean
    for the Pi, per research). Demonstrates a state machine: defined states
    plus guarded transitions."""
    OFF = "off"
    IDLING = "idling"       # engine on, not moving
    DRIVING = "driving"     # moving above threshold
    MENU = "menu"           # user is interacting with the menu


class StateMachine:
    """Manages the current state and the legal transitions between states.
    Deterministic - given a state and an event, the next state is defined."""

    def __init__(self):
        self.__state = AppState.OFF

    @property
    def state(self):
        return self.__state

    def to_menu(self):
        # Entering the menu is allowed from any running state
        self.__state = AppState.MENU

    def resume_from_menu(self, speed, driving_speed=5):
        # Leaving the menu: pick driving vs idling based on speed
        self.__update_driving(speed, driving_speed)

    def update_from_speed(self, speed, driving_speed=5):
        # Only auto-switch driving/idling when NOT in the menu
        if self.__state == AppState.MENU:
            return
        self.__update_driving(speed, driving_speed)

    def __update_driving(self, speed, driving_speed):
        # Private guard: decide driving vs idling from speed
        if speed is not None and speed > driving_speed:
            self.__state = AppState.DRIVING
        else:
            self.__state = AppState.IDLING

    def label(self):
        # Human-readable current state (for display)
        if self.__state == AppState.DRIVING:
            return "DRIVING (Highway watch)"
        elif self.__state == AppState.IDLING:
            return "IDLING (Camp watch)"
        elif self.__state == AppState.MENU:
            return "MENU"
        return "OFF"