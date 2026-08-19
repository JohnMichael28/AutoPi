from autopi.config import Config
from autopi.vehicle import Vehicle
from autopi.vehicle_data import VehicleData
from autopi.ai_client import AIClient
from autopi.ai_router import AIRouter
from autopi.ui import run_ui


if __name__ == "__main__":
    config = Config()
    vehicle = Vehicle(port=config.obd_port)

    print("Connecting to vehicle...")
    print("Status:", vehicle.connect())

    ai = AIClient(config)
    provider = VehicleData(vehicle, ai_client=ai)
    router = AIRouter(ai)

    run_ui(provider, vehicle=vehicle, ai_client=ai, ai_router=router)