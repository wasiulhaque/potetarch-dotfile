import json
from pathlib import Path
import sys
from time import sleep
from urllib.parse import urlencode
from urllib.request import urlopen

conditions = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog",
    51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Snow", 73: "Snow", 75: "Heavy snow", 77: "Snow",
    80: "Showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorms", 96: "Thunderstorms", 99: "Thunderstorms",
}

def fetch_weather(latitude, longitude):
    query = urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,weather_code",
        "temperature_unit": "celsius",
    })
    with urlopen(f"https://api.open-meteo.com/v1/forecast?{query}", timeout=8) as response:
        current = json.load(response)["current"]
    temperature = current["temperature_2m"]
    condition = conditions.get(current["weather_code"], "")
    return f"{temperature:.0f}°C {condition}".rstrip()


def main():
    location_file = Path(__file__).with_name("weather-location.json")
    try:
        location = json.loads(location_file.read_text())
        latitude = float(location["latitude"])
        longitude = float(location["longitude"])
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("Coordinates out of range")
    except (OSError, ValueError, TypeError, KeyError):
        print("Set weather location", flush=True)
        if "--follow" in sys.argv[1:]:
            # Keep the module alive while waiting for the user to restart Waybar.
            while True:
                sleep(3600)
        return

    follow = "--follow" in sys.argv[1:]
    while True:
        try:
            weather = fetch_weather(latitude, longitude)
        except (OSError, ValueError, KeyError, TypeError):
            print("Weather unavailable", flush=True)
            delay = 15
        else:
            print(weather, flush=True)
            delay = 900

        if not follow:
            return
        sleep(delay)


if __name__ == "__main__":
    main()
