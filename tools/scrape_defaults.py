#!/usr/bin/env python3
"""
Download default MvC2 palettes from zachd.com/palmod/default/ and save
them into the merged character folders with a 'Default' suffix.

Output filenames: {Character}_{Button}_Default.png
"""
import os
import sys
import urllib.request
import urllib.error

# Map from zachd.com names -> our safe_name format
CHAR_MAP = {
    "akuma": "Akuma",
    "amingo": "Amingo",
    "anakaris": "Anakaris",
    "bbhood": "BB_Hood",
    "blackheart": "Blackheart",
    "bonerine": "Wolverine_Bone_Claw",
    "cable": "Cable",
    "cammy": "Cammy",
    "capam": "Captain_America",
    "capcom": "Captain_Commando",
    "charlie": "Charlie",
    "chunli": "Chun_Li",
    "colossus": "Colossus",
    "cyclops": "Cyclops",
    "dan": "Dan",
    "dhalsim": "Dhalsim",
    "drdoom": "Dr_Doom",
    "felicia": "Felicia",
    "gambit": "Gambit",
    "guile": "Guile",
    "hayato": "Hayato",
    "hulk": "Hulk",
    "iceman": "Iceman",
    "ironman": "Iron_Man",
    "jill": "Jill",
    "jin": "Jin",
    "juggernaut": "Juggernaut",
    "ken": "Ken",
    "magneto": "Magneto",
    "marrow": "Marrow",
    "mbison": "MBison",
    "megaman": "Megaman",
    "morrigan": "Morrigan",
    "omegared": "Omega_Red",
    "psylocke": "Psylocke",
    "rogue": "Rogue",
    "roll": "Roll",
    "rubyheart": "Ruby_Heart",
    "ryu": "Ryu",
    "sabretooth": "Sabretooth",
    "sakura": "Sakura",
    "sentinel": "Sentinel",
    "servbot": "Servbot",
    "shuma": "Shuma_Gorath",
    "silversam": "Silver_Samurai",
    "sonson": "SonSon",
    "spiderman": "Spider_Man",
    "spiral": "Spiral",
    "storm": "Storm",
    "strider": "Strider",
    "thanos": "Thanos",
    "tron": "Tron_Bonne",
    "venom": "Venom",
    "warmachine": "War_Machine",
    "wolverine": "Wolverine",
    "zangief": "Zangief",
}

BUTTONS = ["LP", "LK", "HP", "HK", "A1", "A2"]
BASE_URL = "https://zachd.com/palmod/default"


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "D:/Storage/MvC2Modding/MvC2_Skins/Mixes/merged"

    print("=" * 60)
    print("Downloading default MvC2 palettes")
    print("=" * 60)
    print(f"Source: {BASE_URL}")
    print(f"Output: {out_dir}")
    print()

    downloaded = 0
    errors = []

    for site_name, our_name in sorted(CHAR_MAP.items()):
        char_dir = os.path.join(out_dir, our_name)
        os.makedirs(char_dir, exist_ok=True)

        for btn in BUTTONS:
            # URL has space in filename: "akuma LP.png"
            url = f"{BASE_URL}/{site_name}%20{btn}.png"
            filename = f"{our_name}_{btn}_Default.png"
            filepath = os.path.join(char_dir, filename)

            try:
                urllib.request.urlretrieve(url, filepath)
                downloaded += 1
            except urllib.error.HTTPError as e:
                errors.append((our_name, btn, str(e)))
            except Exception as e:
                errors.append((our_name, btn, str(e)))

        print(f"  {our_name:<25} [6 buttons]")

    print(f"\nDone! Downloaded {downloaded} default palette images")
    if errors:
        print(f"Errors ({len(errors)}):")
        for name, btn, err in errors:
            print(f"  {name} {btn}: {err}")


if __name__ == "__main__":
    main()
