"""Small real source snapshots used by the free local Machine Dashboard demo."""
DEMO_REPOSITORY_NAME = "splash-demo/checkout"
BASE_FILES = {
    "api/machines.py": '''from fastapi import FastAPI

app = FastAPI()

@app.get("/api/machines/current")
async def current_machine():
    return {"machine_id": "MX-17", "temperature": 72.4, "status": "running"}
''',
    "web/MachineDashboard.tsx": '''type MachineReading = { machine_id: string; temperature: number; status: string };

export async function loadMachineDashboard() {
  const response = await fetch("/api/machines/current");
  const machine: MachineReading = await response.json();
  return `${machine.machine_id}: ${machine.temperature.toFixed(1)}°C (${machine.status})`;
}
''',
}
RENAME_FILES = {
    **BASE_FILES,
    "api/machines.py": BASE_FILES["api/machines.py"].replace('"temperature": 72.4', '"current_temperature": 72.4'),
}
SAFE_FILES = {
    **BASE_FILES,
    "api/machines.py": BASE_FILES["api/machines.py"].replace('"status": "running"', '"status": "running", "uptime_seconds": 3600'),
}
