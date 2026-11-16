from autopi.config import Config
from autopi.vehicle import Vehicle
from autopi.vehicle_data import VehicleData
from autopi.ai_client import AIClient
from autopi.ai_router import AIRouter
from autopi.ui import run_ui


if __name__ == "__main__":
    config = Config()
    import json
    with open("config.json") as f:
        poll = json.load(f).get("poll", {})
    # fast=True: python-OBD tells the adapter how many replies to expect, so
    # it answers immediately instead of waiting out its timeout on every PID.
    # If reads ever hang again on an older ELM327 clone, set obd_fast false.
    vehicle = Vehicle(port=config.obd_port,
                      fast=poll.get("obd_fast", True),
                      timeout=poll.get("obd_timeout_s", 1.0),
                      protocol=poll.get("obd_protocol"))

    print("Connecting to vehicle...")
    print("Status:", vehicle.connect())
    if poll.get("fast_lane", True):
        vehicle.start_fast_lane(gap_s=poll.get("fast_lane_gap_s", 0.005))

    ai = AIClient(config)
    provider = VehicleData(vehicle, ai_client=ai)
    router = AIRouter(ai)

    run_ui(provider, vehicle=vehicle, ai_client=ai, ai_router=router)