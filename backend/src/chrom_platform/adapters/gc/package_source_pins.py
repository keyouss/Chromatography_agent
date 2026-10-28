"""Independent fixed-GC package code inventory.

The build integrity inventory must anchor this pin module itself; it is the
trust root for checking the listed verifier and runtime source before use.
"""

PACKAGE_SOURCE_SHA256: dict[str, str] = {
    "chrom_platform/adapters/gc/package_loader.py": "baf935d18886b6a2f6a80a06def2d13167b3ea018b6f338b3a99b76561db68b1",
    "chrom_platform/adapters/gc/package_model.py": "c89794db0fed2ad99f072861d5a39bbefebf50b4af2462bc9874c2b63f8ee361",
    "chrom_platform/adapters/gc/runtime.py": "d63f1f523d355b1e5a13a5dfb4d938cdc237e66f2ce7454a21f3b308bc54eea2",
    "chrom_platform/adapters/gc/manifest.py": "5e9935d02eac84fa041305d64e5c9c4b9320fb5b039e6eb9e6cc6aa0c4ec55b7",
    "chrom_platform/adapters/gc/serving_profile.py": "858f8f302f402cfc6ec731ffe959983963813882c989f02c7177c8536bbc5c76",
    "chrom_platform/adapters/gc/source_pins.py": "3f6bd57d572338ff861f0be4987c8ee1c28ba5bbbb4f4b76a30c6a22f89bcd90",
    "chrom_platform/adapters/gc/validation.py": "0239bd6278a212d0662b13929487f97e4c0dbc1a9150b84e58b92abaa557118a",
    "chrom_platform/core/model_load_observation.py": "55b5e3a0399e6c06aeea822dfa4ad37b314591917238ee2924ab6f72074a791b",
    "chrom_platform/artifact_core/model_package_manifest.py": "5ffa28cabbe999454106bb0667820be8028936d2aab58e6287f55887e39af2f2",
    "chrom_platform/modules/model_center/package_verification.py": "08df9fae02e8167ebba603af10063e25e0ceba2cc8d635f2f0569ad63e0dbebe",
    "chrom_platform/modules/prediction/schemas.py": "bcb8dfb03214fafc6b542f721e0207075f3e351fa96d52bc00c6ad9d86531b50",
}

__all__ = ["PACKAGE_SOURCE_SHA256"]
