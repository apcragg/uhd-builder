#!/usr/bin/env python3
import argparse
import logging
import os
import shutil
import subprocess
import sys
import json
import re
import zipfile
import hashlib
import base64
import csv
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

    def run(self, cmd, cwd=None, env=None, capture_output=False):
        logger.info(f"Running: {' '.join(cmd)}")
        if capture_output:
            return subprocess.check_output(cmd, cwd=cwd, env=env or os.environ, text=True)
        subprocess.check_call(cmd, cwd=cwd, env=env or os.environ)

    def patch_file(self, file_path, patch_code, marker, regex=None):
        """
        Safely patches a file by injecting code or applying regex replacements.
        Ensures patches are not applied multiple times.
        """
        if not file_path.exists():
            return False

        content = file_path.read_text()
        
        # Apply regex replacements if provided
        if regex:
            for pattern, replacement in regex.items():
                content = re.sub(pattern, replacement, content)

        # Inject patch code if marker not present
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
        if not self.args.incremental and self.build_dir.exists():
            shutil.rmtree(self.build_dir)
        self.build_dir.mkdir(parents=True, exist_ok=True)

        import sysconfig
        py_inc = sysconfig.get_path('include')
        py_libdir = sysconfig.get_config_var('LIBDIR')
        py_ldlibrary = sysconfig.get_config_var('LDLIBRARY')
        py_lib = None
        if py_libdir and py_ldlibrary:
            py_lib = os.path.join(py_libdir, py_ldlibrary)

        logger.info(f"Python Include: {py_inc}")
        
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
        
        # Try to find installed package
        site_pkgs = list(self.install_dir.glob("**/site-packages/uhd")) or \
                    list(self.install_dir.glob("**/dist-packages/uhd"))
        
        uhd_dest = self.staging_dir / "uhd"
        
        if site_pkgs and (site_pkgs[0] / "__init__.py").exists():
            shutil.copytree(site_pkgs[0], uhd_dest, dirs_exist_ok=True)
        else:
            logger.warning("Could not find installed 'uhd' package in DESTDIR. Reconstructing from build artifacts.")
            # Fallback: Copy from BUILD directory (which has generated __init__.py)
            # CMake usually stages the python package in host/build/python/uhd
            build_python_pkg = self.build_dir / "python" / "uhd"
            if build_python_pkg.exists():
                shutil.copytree(build_python_pkg, uhd_dest, dirs_exist_ok=True)
                logger.info(f"Copied python package from {build_python_pkg}")
            else:
                # Last resort: Copy from source (will have unconfigured __init__.py.in, but better than nothing?)
                # No, that's broken. Raise error.
                raise RuntimeError(f"Could not find python package in {build_python_pkg}")

            # Copy compiled extension from build dir if not already there
            # (It might be in build/python/uhd/ or just build/python/)
            build_python = self.build_dir / "python"
            found_ext = list(build_python.glob("libpyuhd*.so"))
            if found_ext:
                shutil.copy(found_ext[0], uhd_dest / found_ext[0].name)
                logger.info(f"Recovered extension module: {found_ext[0].name}")

        # Double check extension exists in dest
        if not list(uhd_dest.glob("libpyuhd*.so")):
             # Try one more time to find it in install dir broadly
             found_ext = list(self.install_dir.glob("**/libpyuhd*.so"))
             if found_ext:
                 shutil.copy(found_ext[0], uhd_dest / found_ext[0].name)
             else:
                 # Check build dir again if we took the first branch
                 build_python = self.build_dir / "python"
                 found_ext = list(build_python.glob("libpyuhd*.so"))
                 if found_ext:
                     shutil.copy(found_ext[0], uhd_dest / found_ext[0].name)
                 else:
                     logger.error("Failed to locate libpyuhd.so!")

        images_dir = uhd_dest / "share" / "uhd" / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        if not (images_dir / "inventory.json").exists():
            (images_dir / "inventory.json").write_text("{}")

        utils_dest = uhd_dest / "utils"
        utils_dest.mkdir(exist_ok=True)
        (utils_dest / "__init__.py").touch()

        # Copy utilities
        for py_util in self.install_dir.glob("**/uhd/utils/*.py"):
            shutil.copy(py_util, utils_dest / py_util.name)
        
        # Patch uhd_images_downloader.py
        downloader = utils_dest / "uhd_images_downloader.py"
        if downloader.exists():
            lines = downloader.read_text().splitlines()
            new_lines = []
            imported_sys = False
            
            for line in lines:
                if line.strip().startswith("import sys"):
                    imported_sys = True
                
                # Replace the install path line
                if line.strip().startswith("_DEFAULT_INSTALL_PATH"):
                    new_lines.append('_DEFAULT_INSTALL_PATH = os.path.join(sys.prefix, "share", "uhd", "images")')
                else:
                    new_lines.append(line)
            
            # Inject import sys if missing
            if not imported_sys:
                # Find a good place (after shebang/comments)
                insert_idx = 0
                for i, line in enumerate(new_lines):
                    if line.strip() and not line.startswith("#") and not line.startswith('"""'):
                        insert_idx = i
                        break
                new_lines.insert(insert_idx, "import sys")
                
            downloader.write_text("\n".join(new_lines))
            logger.info("Patched uhd_images_downloader.py with sys.prefix path")

        self.found_utils = []
        for line in installed_files:
            if "/bin/" in line or "/examples/" in line:
                src_path = self.install_dir / line.lstrip('/')
                if src_path.exists() and not src_path.is_dir():
                    util_name = src_path.name
                    if util_name.endswith('.py') or util_name.endswith('.sh') or util_name.endswith('.cpp') or util_name.endswith('.h') or util_name.endswith('.txt'):
                        continue
                    
                    # We NO LONGER copy binaries to utils_dest here. 
                    # They will be injected into .data/scripts later.
                    if util_name not in self.found_utils:
                        self.found_utils.append(util_name)
                        logger.info(f"Found utility/example (will be bundled in scripts): {util_name}")

        if "usrpctl" not in self.found_utils:
             usrpctl = self.install_dir / "usr" / "bin" / "usrpctl"
             if usrpctl.exists():
                 self.found_utils.append("usrpctl")

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

        # Entry points ONLY for python scripts now
        # We point directly to the module main function
        # Note: uhd_images_downloader.py usually has a main()
        eps = [
            "uhd_images_downloader = uhd.utils.uhd_images_downloader:main"
        ]
        
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

    def resolve_dependencies(self, lib_path):
        """
        Recursively find required shared libraries (excluding system ones).
        """
        deps = set()
        out = self.run(["patchelf", "--print-needed", str(lib_path)], capture_output=True)
        for line in out.splitlines():
            line = line.strip()
            # Filter out system libraries
            if any(line.startswith(p) for p in ["libc.so", "libdl.so", "libm.so", "libpthread.so", "librt.so", "libgcc_s.so", "libstdc++.so", "ld-linux"]):
                continue
            if line.startswith("libpython"):
                continue
            
            # Find the library in LD_LIBRARY_PATH-ish locations
            found = False
            for search_path in ["/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu", "/usr/lib", "/usr/local/lib"]:
                candidate = Path(search_path) / line
                if candidate.exists():
                    deps.add(candidate)
                    # Recursively add deps of deps (limited depth implicit)
                    # Simple single-pass for now as we mostly care about boost/usb
                    found = True
                    break
            if not found:
                logger.warning(f"Could not find dependency {line} for {lib_path}")
        return deps

    def restructure_wheel(self):
        wheels = list(self.dist_dir.glob("*.whl"))
        if not wheels:
            logger.error("No wheels found to restructure!")
            return
        
        wheel_path = wheels[0]
        wheel_name = wheel_path.name
        
        # Determine platform tag from name or system
        # We need a proper platform tag for the wheel to be installable
        try:
            libc_ver = subprocess.check_output([self.python_exec, "-c", "import platform; print('_'.join(platform.libc_ver()[1].split('.')[:2]))"], text=True).strip()
        except Exception:
            libc_ver = "2_17"
        arch = subprocess.check_output(["uname", "-m"], text=True).strip().replace("arm64", "aarch64")
        plat_tag = f"manylinux_{libc_ver}_{arch}"

        # Unpack
        unpack_dir = self.dist_dir / "unpacked"
        if unpack_dir.exists():
            shutil.rmtree(unpack_dir)
        unpack_dir.mkdir()
        
        with zipfile.ZipFile(wheel_path, 'r') as zf:
            zf.extractall(unpack_dir)
        
        wheel_path.unlink() # Delete original

        # Create .data structure
        dist_info_dir = list(unpack_dir.glob("*.dist-info"))[0]
        # The data directory must be named exacty {distribution}-{version}.data
        # This matches the prefix of the .dist-info directory.
        data_dir_name = dist_info_dir.name.replace(".dist-info", ".data")
        data_dir = unpack_dir / data_dir_name
        
        # We put headers in data/include so they end up in $VIRTUAL_ENV/include
        # instead of $VIRTUAL_ENV/include/site/pythonX.Y/uhd/
        headers_dest = data_dir / "data" / "include" / "uhd"
        scripts_dest = data_dir / "scripts"
        lib_dest = data_dir / "data" / "lib"
        share_dest = data_dir / "data" / "share"
        cmake_dest = lib_dest / "cmake" / "uhd"
        pkgconfig_dest = lib_dest / "pkgconfig"

        headers_dest.parent.mkdir(parents=True, exist_ok=True)
        scripts_dest.mkdir(parents=True, exist_ok=True)
        lib_dest.mkdir(parents=True, exist_ok=True)
        share_dest.parent.mkdir(parents=True, exist_ok=True)
        cmake_dest.parent.mkdir(parents=True, exist_ok=True)
        pkgconfig_dest.parent.mkdir(parents=True, exist_ok=True)

        # Headers
        # Copy from install_prefix/usr/include/uhd -> .data/headers/uhd
        src_include = self.install_dir / "usr" / "include" / "uhd"
        if src_include.exists():
            shutil.copytree(src_include, headers_dest, dirs_exist_ok=True)

        # Binaries (Scripts)
        # Copy from install_prefix/usr/bin + examples -> .data/scripts
        # We use the list we found earlier or just glob
        for item in self.found_utils:
            # Find it in install_dir
            found = list(self.install_dir.glob(f"**/{item}"))
            if found:
                # If multiple, prefer bin
                src = next((f for f in found if "/bin/" in str(f)), found[0])
                if src.is_file():
                     shutil.copy(src, scripts_dest / item)
                     # Binaries need $ORIGIN/../lib RPATH
                     # Check if it is an ELF file first
                     with open(scripts_dest / item, 'rb') as f:
                         magic = f.read(4)
                     if magic == b'\x7fELF':
                         self.run(["patchelf", "--set-rpath", "$ORIGIN/../lib", str(scripts_dest / item)])
                         # Resolve dependencies for this binary too (e.g. boost_program_options)
                         deps = self.resolve_dependencies(src)
                         for dep in deps:
                             if not (lib_dest / dep.name).exists():
                                 shutil.copy(dep, lib_dest / dep.name)
                                 self.run(["patchelf", "--set-rpath", "$ORIGIN", str(lib_dest / dep.name)])

        # Libraries
        # Copy libuhd.so -> .data/data/lib
        libuhd_src = list(self.install_dir.glob("**/libuhd.so*"))
        if libuhd_src:
            # Sort to get the main .so last or first? usually we want the real file
            # We copy all symlinks and real files
            for lib in libuhd_src:
                # If it's a symlink, we usually want to resolve it or skip if it duplicates
                # BUT for libuhd.so (linker name), we MUST have it.
                if lib.is_symlink():
                     if lib.name == "libuhd.so":
                         # Create a copy of the target as libuhd.so
                         target = lib.resolve()
                         shutil.copy(target, lib_dest / "libuhd.so")
                         self.run(["patchelf", "--set-rpath", "$ORIGIN", str(lib_dest / "libuhd.so")])
                         logger.info("Created libuhd.so (linker name)")
                     continue
                
                # Copy real file
                if not lib.is_symlink():
                    shutil.copy(lib, lib_dest / lib.name)
                    # Bundle dependencies for libuhd.so (only for the real one)
                    if "libuhd.so" in lib.name:
                        deps = self.resolve_dependencies(lib)
                        for dep in deps:
                            if not (lib_dest / dep.name).exists():
                                shutil.copy(dep, lib_dest / dep.name)
                                self.run(["patchelf", "--set-rpath", "$ORIGIN", str(lib_dest / dep.name)])

                    # Patch libuhd.so RPATH
                    self.run(["patchelf", "--set-rpath", "$ORIGIN", str(lib_dest / lib.name)])

            # Ensure symlinks exist for versioned SOs
            # libuhd.so -> libuhd.so.4.x.x
            # We will rely on whatever glob found. 
            
        # CMake & PkgConfig
        # Robustly find UHDConfig.cmake
        cmake_files = list(self.install_dir.rglob("UHDConfig.cmake"))
        if cmake_files:
            # We found it, copy the directory containing it
            # e.g. .../cmake/uhd/UHDConfig.cmake -> copy .../cmake/uhd
            shutil.copytree(cmake_files[0].parent, cmake_dest, dirs_exist_ok=True)
            logger.info(f"Bundled CMake config from {cmake_files[0].parent}")
        else:
            logger.warning("Could not find UHDConfig.cmake!")
        
        # Robustly find uhd.pc
        pc_files = list(self.install_dir.rglob("uhd.pc"))
        if pc_files:
             shutil.copytree(pc_files[0].parent, pkgconfig_dest, dirs_exist_ok=True)
             logger.info(f"Bundled PkgConfig from {pc_files[0].parent}")
        else:
            logger.warning("Could not find uhd.pc!")

        # Share (Images)
        # Copy install_prefix/usr/share/uhd -> .data/data/share/uhd
        src_share = list(self.install_dir.glob("**/share/uhd"))
        if src_share:
            # Install to system share (for libuhd)
            shutil.copytree(src_share[0], share_dest / "uhd", dirs_exist_ok=True)
            
            # Install to python package share (for python scripts/legacy compat)
            # e.g. site-packages/uhd/share/uhd
            python_share_dest = unpack_dir / "uhd" / "share" / "uhd"
            if not python_share_dest.exists():
                shutil.copytree(src_share[0], python_share_dest, dirs_exist_ok=True)
                logger.info(f"Duplicated share dir to {python_share_dest}")

        # Patch Python Extension RPATH
        # find _uhd.so or libpyuhd.so in site-packages/uhd
        for ext in (unpack_dir / "uhd").glob("libpyuhd*.so"):
            # It needs to find libuhd.so in .venv/lib
            # Layout: .venv/lib/pythonX.Y/site-packages/uhd/libpyuhd.so
            # $ORIGIN = uhd
            # ../ = site-packages
            # ../../ = pythonX.Y
            # ../../../ = lib  <-- This is where libuhd.so is
            self.run(["patchelf", "--set-rpath", "$ORIGIN/../../..", str(ext)])

        # Update RECORD
        self.update_record(unpack_dir, dist_info_dir)
        
        # Repack
        new_wheel_name = wheel_name.replace("linux_x86_64", plat_tag).replace("linux_aarch64", plat_tag)
        if "manylinux" not in new_wheel_name:
             # Force rename if it was linux_x86_64
             parts = wheel_name.split('-')
             # uhd-4.6.0.0-cp312-cp312-linux_x86_64.whl
             parts[-1] = plat_tag + ".whl"
             new_wheel_name = "-".join(parts)

        new_wheel_path = self.dist_dir / new_wheel_name
        self.pack_wheel(unpack_dir, new_wheel_path)
        shutil.rmtree(unpack_dir)
        logger.info(f"Created restructured wheel: {new_wheel_path}")

    def update_record(self, root, dist_info):
        record_path = dist_info / "RECORD"
        # We essentially regenerate it
        with open(record_path, 'w', newline='') as f:
            writer = csv.writer(f)
            for path in root.rglob("*"):
                if path.is_file():
                    if path.name == "RECORD":
                        continue
                    rel_path = path.relative_to(root)
                    data = path.read_bytes()
                    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode('ascii').rstrip('=')
                    writer.writerow([str(rel_path), f"sha256={digest}", len(data)])
            writer.writerow([str(record_path.relative_to(root)), "", ""])

    def pack_wheel(self, source_dir, out_path):
        with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for path in source_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(source_dir))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="v4.6.0.0")
    parser.add_argument("--numpy-spec", default="<2")
    parser.add_argument("--incremental", action="store_true", help="Keep build directory for faster re-runs")
    args = parser.parse_args()

    builder = UHDWheelBuilder(args)
    builder.setup_source()
    builder.patch_cmake_files()
    builder.build_native()
    builder.assemble_package()
    builder.build_wheel()
    builder.restructure_wheel() # Replaces repair_wheel

if __name__ == "__main__":
    main()