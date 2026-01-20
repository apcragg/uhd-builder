# UHD Python Wheel Builder

This project provides a robust, Docker-based build system for creating relocatable Python wheels for the USRP Hardware Driver (UHD). 

## Features

*   **Multi-Platform Support**: Build wheels for different Ubuntu versions (20.04, 22.04, 24.04).
*   **Multi-Python Support**: Target specific Python versions using `uv`.
*   **Relocatable**: Bundles UHD binary utilities and C++ shared libraries into the wheel.
*   **Automatic Image Discovery**: Utilities automatically find bundled FPGA images and firmware.
*   **Optimized Build Cache**: Docker layers are optimized for fast subsequent builds.

## Prerequisites

*   Docker
*   Python 3.8+ (on host)
*   `uv` or `pip` for verification

## Usage

### Building a Wheel

Run the `build.py` script to build a wheel for a specific Ubuntu, Python, Numpy, and UHD target:

```bash
# Build for Ubuntu 24.04 (Python 3.12)
python3 build.py --ubuntu 24.04 --tag v4.6.0.0

# Build for Ubuntu 22.04 with Python 3.11
python3 build.py --ubuntu 22.04 --python 3.11 --tag v4.6.0.0
```

Resulting wheels are placed in the `dist/` directory.

### Advanced Options

*   `--arch`: Specify target architecture (`x86_64` or `arm64`).
*   `--numpy`: Specify NumPy version constraint (default: `numpy<2`).
*   `--clean`: Remove build artifacts and cache.\
*   `--tag`: UHD version tag (`v4.9.0.1`)

## Why the "relocatability patches"?

Official UHD wheels on PyPI are essentially just the Python libraries;. They require that you've already installed the UHD system libraries on your machine.

This builder creates **"Fat Wheels"** that are totally self-contained.

1.  **Library paths**: We use `auditwheel` to vendor every `.so` file into the wheel and rewrite `RPATH` so they find each other.
2.  **Resource paths (The "Hack")**: UHD expects FPGA images in `/usr/share/uhd`. When you're in a virtualenv, they aren't there. We inject a small `relocation.py` script that triggers on `import uhd`. It figures out where the package was installed and sets `UHD_IMAGES_DIR` automatically.

The result is that you can just `pip install` and everything, including the binary utilities and FPGA loading, just works.

## Verification

Verify the built wheel using `uv`:

```bash
uv venv test_env --python 3.12
uv pip install --python test_env dist/uhd-*.whl
uv run --python test_env uhd_images_downloader -t b2x
uv run --python test_env uhd_find_devices
```
Note that you should download images for the type of device you have. To download *all* images, omit the `-t` flag.

## License

This project is licensed under the GPLv3, matching the UHD license.
