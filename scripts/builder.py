#!/usr/bin/env python3
import argparse
import logging
import os
import shutil
import subprocess
import sys
import json
import re
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

class UHDWheelBuilder:
    def __init__(self, args):
        self.args = args
        self.root_dir = Path.cwd()
        self.scripts_dir = Path(__file__).parent.absolute()
        self.uhd_dir = self.root_dir / "uhd"
        self.build_dir = self.uhd_dir / "host" / "build"
        self.install_dir = self.root_dir / "install_prefix"
        self.staging_dir = self.root_dir / "staging"
        self.dist_dir = self.root_dir / "dist"
        self.python_exec = sys.executable
        self.found_utils = []

    def run(self, cmd, cwd=None, env=None):
        logger.info(f"Running: {' '.join(cmd)}")
        subprocess.check_call(cmd, cwd=cwd, env=env or os.environ)

    def patch_file(self, file_path, patch_code, marker, regex=None):
        """
        Safely patches a file by injecting code or applying regex replacements.
        Ensures patches are not applied multiple times.
        """
        if not file_path.exists():
            return False

        content = file_path.read_text()
        
        # 1. Apply regex replacements if provided
        if regex:
            for pattern, replacement in regex.items():
                content = re.sub(pattern, replacement, content)

        # 2. Inject patch code if marker not present
        if marker and marker not in content:
            lines = content.splitlines()
            # Find insertion point: after __future__ or top-level docstring/comments
            insert_idx = 0
            for i, line in enumerate(lines):
                if "__future__" in line:
                    insert_idx = i + 1
                elif line.strip() and not line.startswith("#") and not (line.startswith('"""') or line.startswith("'''")):
                    # Stop at first real code, but if we haven't found a better place, use this
                    if insert_idx == 0:
                        insert_idx = i
                    break
            
            lines.insert(insert_idx, f"\n{marker}\n{patch_code}\n")
            content = "\n".join(lines)
        
        file_path.write_text(content)
        return True

    def setup_source(self):
        if not self.uhd_dir.exists():
            self.uhd_dir.mkdir(parents=True)

        if not (self.uhd_dir / ".git").exists():
            if self.uhd_dir.exists() and any(self.uhd_dir.iterdir()):
                logger.warning(f"{self.uhd_dir} is not empty but not a git repo. Cleaning it...")
                for item in self.uhd_dir.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
            
            logger.info(f"Cloning UHD into {self.uhd_dir}...")
            self.run(["git", "clone", "https://github.com/EttusResearch/uhd.git", "."], cwd=self.uhd_dir)
        
        self.run(["git", "fetch", "origin"], cwd=self.uhd_dir)
        self.run(["git", "checkout", "-f", self.args.tag], cwd=self.uhd_dir)
        self.run(["git", "submodule", "update", "--init", "--recursive"], cwd=self.uhd_dir)

    def patch_cmake_files(self):
        # Disable virtualenv detection to force installation to DESTDIR
        cmake_file = self.uhd_dir / "host" / "python" / "CMakeLists.txt"
        if cmake_file.exists():
            content = cmake_file.read_text()
            # Force HAVE_PYTHON_VIRTUALENV to FALSE after the check
            if "PYTHON_CHECK_MODULE" in content and "HAVE_PYTHON_VIRTUALENV" in content:
                # We append the override after the check
                pattern = 'HAVE_PYTHON_VIRTUALENV\n)'
                replacement = 'HAVE_PYTHON_VIRTUALENV\n)\nset(HAVE_PYTHON_VIRTUALENV FALSE)'
                if "set(HAVE_PYTHON_VIRTUALENV FALSE)" not in content:
                    content = content.replace(pattern, replacement)
                    cmake_file.write_text(content)
                    logger.info("Patched host/python/CMakeLists.txt to disable venv detection")

    def build_native(self):
        if self.build_dir.exists():
            shutil.rmtree(self.build_dir)
        self.build_dir.mkdir(parents=True)

        import sysconfig
        py_inc = sysconfig.get_path('include')
        py_libdir = sysconfig.get_config_var('LIBDIR')
        py_ldlibrary = sysconfig.get_config_var('LDLIBRARY')
        py_lib = None
        if py_libdir and py_ldlibrary:
            py_lib = os.path.join(py_libdir, py_ldlibrary)

        logger.info(f"Python Include: {py_inc}")
        logger.info(f"Python Library: {py_lib}")
        
        cmake_cmd = [
            "cmake", "..",
            "-DENABLE_PYTHON_API=ON",
            "-DENABLE_C_API=ON",
            "-DENABLE_EXAMPLES=ON",
            "-DENABLE_TESTS=OFF",
            "-DENABLE_MANUAL=OFF",
            "-DENABLE_DOXYGEN=OFF",
            "-DENABLE_SIM=OFF",
            "-DCMAKE_INSTALL_PREFIX=/usr",
            f"-DPYTHON_EXECUTABLE={self.python_exec}",
            f"-DPYTHON_INCLUDE_DIR={py_inc}",
            f"-DUHD_PYTHON_DIR=lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages",
            "-DCMAKE_BUILD_TYPE=Release"
        ]
        
        # if py_lib and os.path.exists(py_lib):
        #     cmake_cmd.append(f"-DPYTHON_LIBRARY={py_lib}")
        # elif py_libdir:
        #      cmake_cmd.append(f"-DPYTHON_LIBRARY={py_libdir}")

        self.run(cmake_cmd, cwd=self.build_dir)
        self.run(["make", "-j", str(os.cpu_count())], cwd=self.build_dir)
        
        if self.install_dir.exists():
            shutil.rmtree(self.install_dir)
        self.run(["make", "install", f"DESTDIR={self.install_dir}"], cwd=self.build_dir)

    def get_installed_files(self):
        manifest = self.build_dir / "install_manifest.txt"
        if not manifest.exists():
            return []
        return manifest.read_text().splitlines()

    def get_version(self):
        try:
            version = subprocess.check_output(
                ["git", "describe", "--tags", "--always", "--dirty"],
                cwd=self.uhd_dir, text=True
            ).strip()
            if version.startswith('v'):
                version = version[1:]
            if '-' in version:
                parts = version.split('-')
                version = f"{parts[0]}.post{parts[1]}+{parts[2]}"
            return version
        except Exception:
            pass

        for vf in [self.uhd_dir / "VERSION", self.uhd_dir / "host" / "VERSION"]:
            if vf.exists():
                return vf.read_text().strip()
        
        return "4.6.0.0"

    def assemble_package(self):
        if self.staging_dir.exists():
            shutil.rmtree(self.staging_dir)
        self.staging_dir.mkdir()

        installed_files = self.get_installed_files()
        
        site_pkgs = list(self.install_dir.glob("**/site-packages/uhd")) or \
                    list(self.install_dir.glob("**/dist-packages/uhd"))
        if not site_pkgs:
            site_pkgs = list(self.install_dir.glob("**/uhd"))
            if not site_pkgs:
                raise RuntimeError(f"Could not find installed uhd package in {self.install_dir}")
        
        uhd_dest = self.staging_dir / "uhd"
        shutil.copytree(site_pkgs[0], uhd_dest, dirs_exist_ok=True)

        for so_file in self.install_dir.glob("**/libpyuhd*.so"):
             logger.info(f"Found libpyuhd: {so_file}")
             shutil.copy(so_file, uhd_dest / so_file.name)

        images_dir = uhd_dest / "share" / "uhd" / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        if not (images_dir / "inventory.json").exists():
            (images_dir / "inventory.json").write_text("{}")

        utils_dest = uhd_dest / "utils"
        utils_dest.mkdir(exist_ok=True)
        (utils_dest / "__init__.py").touch()

        # Copy relocation helper
        shutil.copy(self.scripts_dir / "relocation.py.template", utils_dest / "relocation.py")

        # Patch uhd/__init__.py
        init_py = uhd_dest / "__init__.py"
        reloc_marker = "# --- UHD RELOCATABILITY PATCH ---"
        reloc_code = "from .utils.relocation import patch_environ; patch_environ()"
        if not init_py.exists():
            init_py.write_text(f"{reloc_marker}\n{reloc_code}")
        else:
            self.patch_file(init_py, reloc_code, reloc_marker)

        # Copy utilities
        for py_util in self.install_dir.glob("**/uhd/utils/*.py"):
            shutil.copy(py_util, utils_dest / py_util.name)
        
        # Patch uhd_images_downloader.py
        downloader = utils_dest / "uhd_images_downloader.py"
        if downloader.exists():
            self.patch_file(
                downloader,
                patch_code="from uhd.utils.relocation import patch_environ; patch_environ()",
                marker="# --- RELOCATABILITY PATCH ---",
                regex={
                    r'"\(fpga\|fw\|windrv\)_default"': r'"(fpga|fw|windrv|b2xx)_default"'
                }
            )

        self.found_utils = []
        for line in installed_files:
            if "/bin/" in line or "/examples/" in line:
                src_path = self.install_dir / line.lstrip('/')
                if src_path.exists() and not src_path.is_dir():
                    util_name = src_path.name
                    # Filter out python/shell scripts that are likely source or build helper scripts,
                    # but keep compiled binaries (which usually have no extension or are .exe on windows, but we are on linux).
                    # Actually, some examples might be python scripts, but usually in examples/ they are C++.
                    if util_name.endswith('.py') or util_name.endswith('.sh') or util_name.endswith('.cpp') or util_name.endswith('.h') or util_name.endswith('.txt'):
                        continue
                    
                    shutil.copy(src_path, utils_dest / util_name)
                    # Deduplicate if listed multiple times (e.g. symlinks)
                    if util_name not in self.found_utils:
                        self.found_utils.append(util_name)
                        logger.info(f"Bundled utility/example: {util_name}")

        if "usrpctl" not in self.found_utils:
             usrpctl = self.install_dir / "usr" / "bin" / "usrpctl"
             if usrpctl.exists():
                 shutil.copy(usrpctl, utils_dest / "usrpctl")
                 self.found_utils.append("usrpctl")

        wrapper_template = (self.scripts_dir / "run_util.py.template").read_text()
        (self.staging_dir / "_uhd_cli.py").write_text(wrapper_template)

        version = self.get_version()

        for license_file in [self.uhd_dir / "LICENSE", self.uhd_dir / "host" / "LICENSE", self.uhd_dir / "COPYING"]:
            if license_file.exists():
                shutil.copy(license_file, self.staging_dir / "LICENSE")
                break

        self.write_setup_py(version)

    def write_setup_py(self, version):
        import setuptools
        packages = setuptools.find_packages(where=str(self.staging_dir))
        
        numpy_spec = self.args.numpy_spec
        if not any(c in numpy_spec for c in ['<', '>', '=', '!']):
             numpy_spec = f"=={numpy_spec}"
        if not numpy_spec.startswith('numpy'):
             numpy_spec = f"numpy{numpy_spec}"

        eps = [
            f"{util} = _uhd_cli:main" for util in self.found_utils
        ]
        if "uhd_images_downloader" not in self.found_utils:
            eps.append("uhd_images_downloader = _uhd_cli:main")
        
        entry_points_str = json.dumps(eps, indent=8)

        template = (self.scripts_dir / "setup.py.template").read_text()
        setup_content = template.format(
            version=version,
            packages=packages,
            numpy_spec=numpy_spec,
            entry_points=entry_points_str
        )
        
        (self.staging_dir / "setup.py").write_text(setup_content)

    def build_wheel(self):
        self.run([self.python_exec, "-m", "build", "--wheel", "--outdir", str(self.dist_dir), "--no-isolation"], cwd=self.staging_dir)

    def repair_wheel(self):
        wheels = list(self.dist_dir.glob("*.whl"))
        if not wheels:
            logger.error("No wheels found to repair!")
            return

        lib_paths = set()
        for lib in self.install_dir.glob("**/libuhd.so*"):
            lib_paths.add(str(lib.parent))
        lib_paths.add("/usr/local/lib")
        lib_paths.add("/usr/lib")

        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = ":".join(list(lib_paths) + [env.get("LD_LIBRARY_PATH", "")])

        for wheel in wheels:
            if "manylinux" in wheel.name:
                continue

            try:
                libc_ver = subprocess.check_output([self.python_exec, "-c", "import platform; print('_'.join(platform.libc_ver()[1].split('.')[:2]))"], text=True).strip()
            except Exception:
                libc_ver = "2_17"
            
            arch = subprocess.check_output(["uname", "-m"], text=True).strip().replace("arm64", "aarch64")
            plat_tag = f"manylinux_{libc_ver}_{arch}"
            
            logger.info(f"Repairing wheel {wheel.name} for platform {plat_tag}")
            self.run(["auditwheel", "repair", str(wheel), "--plat", plat_tag, "-w", str(self.dist_dir)], env=env)
            wheel.unlink()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="v4.6.0.0")
    parser.add_argument("--numpy-spec", default="<2")
    args = parser.parse_args()

    builder = UHDWheelBuilder(args)
    builder.setup_source()
    builder.patch_cmake_files()
    builder.build_native()
    builder.assemble_package()
    builder.build_wheel()
    builder.repair_wheel()

if __name__ == "__main__":
    main()
