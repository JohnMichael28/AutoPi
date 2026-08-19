from autopi.dashboards import (HighwayDashboard, TrackDashboard,
                               AdventureDashboard, CampDashboard)


class DashboardFactory:
    """Factory pattern: creates the right Dashboard subclass from a menu
    choice, hiding the construction. The menu asks the factory instead of
    doing if/elif construction itself. One place to register dashboards."""

    # Map menu keys to dashboard classes (register new ones here in one spot)
    __REGISTRY = {
        "1": HighwayDashboard,
        "2": TrackDashboard,
        "3": AdventureDashboard,
        "4": CampDashboard,
    }

    def __init__(self, vehicle, logger, warning_checker, ai_client, detector,
                 log_every=5):
        # Hold the shared dependencies every dashboard needs
        self._vehicle = vehicle
        self._logger = logger
        self._warning_checker = warning_checker
        self._ai_client = ai_client
        self._detector = detector
        self._log_every = log_every

    def create(self, choice):
        # Return a ready-to-run dashboard for this menu choice, or None
        dashboard_class = self.__REGISTRY.get(choice)
        if dashboard_class is None:
            return None
        # Build it with all the shared dependencies injected
        return dashboard_class(
            self._vehicle, self._logger, self._warning_checker,
            self._ai_client, self._detector, self._log_every)

    def available_choices(self):
        # Which keys the factory knows about (for menu display)
        return list(self.__REGISTRY.keys())