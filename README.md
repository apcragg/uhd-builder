# UHD Python Wheel Builder

This project provides a robust, Docker-based build system for creating relocatable Python wheels for the Universal Software Radio Peripheral (USRP) Hardware Driver (UHD).

## Features

*   **Multi-Platform Support**: Build wheels for different Ubuntu versions (20.04, 22.04, 24.04).
*   **Multi-Python Support**: Target specific Python versions using `uv`.
*   **Relocatable**: Bundles UHD binary utilities and C++ shared libraries into the wheel.
*   **Automatic Image Discovery**: Utilities automatically find bundled FPGA images and firmware.
*   **Optimized Build Cache**: Docker layers are optimized for fast subsequent builds.

## Prerequisites

*   Docker
*   Python 3.8+ (on host)
*   `uv` (recommended for local verification)

## Usage

### Building a Wheel

Run the `build.py` script to build a wheel for a specific Ubuntu and Python target:

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
*   `--clean`: Remove build artifacts and cache.

## Why the "relocatability patches"?

Official UHD wheels on PyPI are essentially just Python shims; they won't work unless you've already installed the UHD drivers globally on your OS. That's a pain if you want a portable environment.

This builder creates **"Fat Wheels"** that are totally self-contained. To make that happen, we have to deal with two things that C++ libraries usually hate:

1.  **Library paths**: We use `auditwheel` to vendor every `.so` file into the wheel and rewrite `RPATH` so they find each other.
2.  **Resource paths (The "Hack")**: UHD expects FPGA images in `/usr/share/uhd`. When you're in a virtualenv, they aren't there. We inject a small `relocation.py` script that triggers on `import uhd`. It figures out where the package was installed and sets `UHD_IMAGES_DIR` automatically.

The result is that you can just `pip install` and everything—including the binary utilities and FPGA loading—just works.

## Verification

You can verify the built wheel using `uv`:

```bash
uv venv test_env --python 3.12
uv pip install --python test_env dist/uhd-*.whl
uv run --python test_env uhd_find_devices
```

## License

This project is licensed under the GPLv3, matching the UHD license.