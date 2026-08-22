"""
Station registry shared by every job: the archive, the snapshots and the asset build.

The full ForecastEx daily-temperature roster, 38 stations. Fields: ICAO
settlement station, display name, latitude, longitude, IANA timezone, and
the contract's native unit (F for US listings, C for international).

Settlement stations are not always the city's best-known airport: New York
settles at LaGuardia (KLGA), Dallas at DFW (KDFW), Houston at Hobby (KHOU),
Denver at Buckley (KBKF). Do not "correct" these.

Coverage notes that shape what the displays can show, per the .gov-only
constraint: api.weather.gov forecasts exist only for US stations; the NBM
hourly bulletin additionally covers the three Canadian stations; the
remaining international stations get METAR observations only
(aviationweather.gov is global). International contracts quote in Celsius,
so their displays use the Celsius values directly rather than converting.
"""

CITIES = [
    # US, Fahrenheit
    ("KATL", "Atlanta",          33.6403,  -84.4269, "America/New_York",    "F"),
    ("KAUS", "Austin",           30.1830,  -97.6799, "America/Chicago",     "F"),
    ("KBKF", "Denver",           39.8466, -104.6562, "America/Denver",      "F"),
    ("KBNA", "Nashville",        36.1189,  -86.6892, "America/Chicago",     "F"),
    ("KBOS", "Boston",           42.3606,  -71.0106, "America/New_York",    "F"),
    ("KCLT", "Charlotte",        35.2083,  -80.9614, "America/New_York",    "F"),
    ("KCOS", "Colorado Springs", 38.8058, -104.7008, "America/Denver",      "F"),
    ("KDCA", "Washington DC",    38.8483,  -77.0342, "America/New_York",    "F"),
    ("KDFW", "Dallas",           32.8974,  -97.0220, "America/Chicago",     "F"),
    ("KDTW", "Detroit",          42.2314,  -83.3308, "America/New_York",    "F"),
    ("KHOU", "Houston",          29.6375,  -95.2825, "America/Chicago",     "F"),
    ("KJAX", "Jacksonville",     30.4953,  -81.6937, "America/New_York",    "F"),
    ("KLAS", "Las Vegas",        36.0719, -115.1634, "America/Los_Angeles", "F"),
    ("KLAX", "Los Angeles",      33.9381, -118.3889, "America/Los_Angeles", "F"),
    ("KLGA", "New York City",    40.7792,  -73.8800, "America/New_York",    "F"),
    ("KMDW", "Chicago",          41.7842,  -87.7553, "America/Chicago",     "F"),
    ("KMIA", "Miami",            25.7906,  -80.3164, "America/New_York",    "F"),
    ("KMSP", "Minneapolis",      44.8831,  -93.2289, "America/Chicago",     "F"),
    ("KMSY", "New Orleans",      29.9934,  -90.2581, "America/Chicago",     "F"),
    ("KOKC", "Oklahoma City",    35.3886,  -97.6003, "America/Chicago",     "F"),
    ("KPHL", "Philadelphia",     39.8733,  -75.2268, "America/New_York",    "F"),
    ("KPHX", "Phoenix",          33.4278, -112.0035, "America/Phoenix",     "F"),
    ("KSAT", "San Antonio",      29.5328,  -98.4636, "America/Chicago",     "F"),
    ("KSEA", "Seattle",          47.4447, -122.3136, "America/Los_Angeles", "F"),
    ("KSFO", "San Francisco",    37.6196, -122.3656, "America/Los_Angeles", "F"),
    ("PHNL", "Honolulu",         21.3178, -157.9203, "Pacific/Honolulu",    "F"),
    # International, Celsius
    ("CYUL", "Montreal",         45.4678,  -73.7423, "America/Montreal",    "C"),
    ("CYVR", "Vancouver",        49.1939, -123.1840, "America/Vancouver",   "C"),
    ("CYYZ", "Toronto",          43.6759,  -79.6294, "America/Toronto",     "C"),
    ("EDDF", "Frankfurt",        50.0333,    8.5706, "Europe/Berlin",       "C"),
    ("LEMD", "Madrid",           40.4722,   -3.5608, "Europe/Madrid",       "C"),
    ("LFPG", "Paris",            49.0097,    2.5479, "Europe/Paris",        "C"),
    ("OMAA", "Abu Dhabi",        24.4330,   54.6511, "Asia/Dubai",          "C"),
    ("OMDB", "Dubai",            25.2532,   55.3657, "Asia/Dubai",          "C"),
    ("RJTT", "Tokyo",            35.5494,  139.7798, "Asia/Tokyo",          "C"),
    ("VHHH", "Hong Kong",        22.3118,  113.9149, "Asia/Hong_Kong",      "C"),
    ("WSSS", "Singapore",         1.3502,  103.9940, "Asia/Singapore",      "C"),
    ("YSSY", "Sydney",          -33.9399,  151.1753, "Australia/Sydney",    "C"),
]

US_CITIES = [c for c in CITIES if c[5] == "F"]
