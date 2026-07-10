"""
Test script for the local TLC/CC/HPLC prediction services.
"""

import requests

from config import config


def _post(name: str, url: str, payload: dict) -> None:
    print(f"\nTesting {name}: {url}")
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
        print(f"   [OK] {name}: {response.json()}")
    except Exception as exc:
        print(f"   [ERROR] {name}: {exc}")


def test_services() -> None:
    print("=" * 50)
    print("Testing chromatography prediction services")
    print("=" * 50)

    _post(
        "TLC",
        config.TLC_API_URL,
        {
            "smiles": "O=C(C1=CC=CC=C1)CCCl",
            "eluent_ratios": [0.98, 0.02, 0, 0, 0],
        },
    )

    _post(
        "CC",
        config.CC_API_URL,
        {
            "smiles_list": ["CC(C)CCOC(=O)C1=CC=CC=C1", "COC(=O)C1=CC=CC=C1"],
            "eluent_system": "PE/EA 40/10",
            "m_param": 4.0,
            "speed": 8.0,
        },
    )

    _post(
        "HPLC",
        config.HPLC_API_URL,
        {
            "smiles": "CCO,CCCO",
            "eluent": 0.1,
            "speed": 1.0,
            "columns": "ADH,IA",
        },
    )

    print("\n" + "=" * 50)
    print("Test finished")
    print("=" * 50)


if __name__ == "__main__":
    test_services()
