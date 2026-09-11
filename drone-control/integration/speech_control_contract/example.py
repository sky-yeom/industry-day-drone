"""Run with: python example.py (no hardware or network)."""
import asyncio
import json
from contract import Gateway
from mock_service import MockService, PROFILE, SITE, SEQUENCE


async def main():
    gateway = Gateway(MockService())
    print(json.dumps(await gateway.call("drone_get_capabilities", {}), indent=2))
    accepted = await gateway.call("drone_execute_route", {
        "profile_id": PROFILE, "site_revision": SITE, "destination_ids": SEQUENCE,
    }, caller_id="speech-app", request_id="trusted-example-execute-1")
    mission_id = accepted["mission"]["mission_id"]
    print(json.dumps(accepted, indent=2))
    print(json.dumps(await gateway.call("drone_get_status", {}), indent=2))
    print(json.dumps(await gateway.call("drone_stop_mission", {"mission_id": mission_id},
        caller_id="speech-app", request_id="trusted-example-stop-1"), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
