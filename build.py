#!/usr/bin/env python3
import argparse
import subprocess
import os
import sys

UBUNTU_PYTHON_MAP = {
    "20.04": "3.8",
    "22.04": "3.10",
    "24.04": "3.12",
}

def run_command(cmd, cwd=None):
    try:
        subprocess.check_call(cmd, cwd=cwd)
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}")
        sys.exit(1)

def validate_config(args):
    print(f"Config: Ubuntu {args.ubuntu} (Target Python {args.python})")
    
    warnings = []
    spec = args.numpy.replace(" ", "")

    major, minor = None, None
    if args.tag.startswith('v'):
        parts = args.tag[1:].split('.')
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            major, minor = int(parts[0]), int(parts[1])

    if major is not None:
        if major < 4:
            warnings.append(f"UHD {args.tag} (< 4.0) is not supported.")
        if args.ubuntu == "24.04" and minor < 6:
            warnings.append(f"UHD {args.tag} requires 4.6+ for Ubuntu 24.04 (GCC 13).")
        if (">2" in spec or ">=2" in spec) and minor < 7:
             warnings.append(f"UHD {args.tag} requires 4.7+ for NumPy 2.0.")

    if warnings:
        print("\nWARNINGS:")
        for w in warnings:
            print(f"  - {w}")
        if not args.yes:
            if input("\nContinue anyway? [y/N] ").lower() != 'y':
                sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Build UHD Python Wheels")
    parser.add_argument("--ubuntu", default="22.04", help="Ubuntu version baseline")
    parser.add_argument("--python", help="Target Python version (defaults to Ubuntu native)")
    parser.add_argument("--tag", default="v4.6.0.0", help="UHD Git Tag/Branch")
    parser.add_argument("--arch", choices=["x86_64", "arm64"], help="Target Architecture")
    parser.add_argument("--numpy", default="numpy<2", help="NumPy version specifier")
    parser.add_argument("--user", default=f"{os.getuid()}:{os.getgid()}", help="UID:GID for the build")
    parser.add_argument("--skip-build", action="store_true", help="Skip docker build step")
    parser.add_argument("--clean", action="store_true", help="Clean build artifacts")
    parser.add_argument("--yes", "-y", action="store_true", help="Answer yes to all prompts")
    parser.add_argument("--incremental", action="store_true", help="Incremental build (don't clean existing build dir)")
    args = parser.parse_args()

    if args.python:
        if args.python.isdigit():
            args.python = f"3.{args.python}"
    else:
        args.python = UBUNTU_PYTHON_MAP.get(args.ubuntu, "3.12")

    if args.clean:
        for d in ["dist", "uhd-cache"]:
            if os.path.exists(d):
                import shutil
                shutil.rmtree(d)
        sys.exit(0)

    validate_config(args)
    
    project_root = os.getcwd()
    dist_dir = os.path.join(project_root, "dist")
    cache_dir = os.path.join(project_root, "uhd-cache")
    
    if os.path.exists(dist_dir):
        import shutil
        shutil.rmtree(dist_dir)
    os.makedirs(dist_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)

    docker_platform_args = []
    if args.arch:
        plat = "amd64" if args.arch == "x86_64" else "arm64"
        docker_platform_args = ["--platform", f"linux/{plat}"]

    image_name = f"uhd-builder:ubuntu{args.ubuntu}-py{args.python}"
    if args.arch:
        image_name += f"-{args.arch}"

    if not args.skip_build:
        run_command([
            "docker", "build", "-t", image_name,
            "-f", "docker/Dockerfile.ubuntu",
            "--build-arg", f"BASE_IMAGE=ubuntu:{args.ubuntu}",
            "--build-arg", f"PYTHON_VERSION={args.python}",
            "."
        ] + docker_platform_args)

    builder_args = ["/scripts/builder.py", "--tag", args.tag, "--numpy-spec", args.numpy]
    if args.incremental:
        builder_args.append("--incremental")

    run_command([
        "docker", "run", "--rm", "--user", args.user,
        "-e", f"NUMPY_SPEC={args.numpy}",
    ] + docker_platform_args + [
        "-v", f"{dist_dir}:/work/dist",
        "-v", f"{project_root}/scripts:/scripts",
        "-v", f"{cache_dir}:/work/uhd",
        image_name,
        "uv", "run",
        "--python", args.python,
        "--with", "mako",
        "--with", "requests",
        "--with", args.numpy,
        "--with", "ruamel.yaml",
        "--with", "build",
        "--with", "setuptools",
        "--with", "auditwheel",
    ] + builder_args)

    print(f"\nSuccessfully built UHD {args.tag} wheels!")
    print(f"Check the '{dist_dir}' directory for the results.")

if __name__ == "__main__":
    main()
