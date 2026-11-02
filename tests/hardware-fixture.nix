# SPDX-License-Identifier: MIT
{ pkgs }:
{
  driver =
    version:
    pkgs.runCommand "host-driver-${version}" { nativeBuildInputs = [ pkgs.stdenv.cc ]; } ''
      mkdir -p $out/usr/lib
      echo 'int impurifier_driver_version(void) { return ${version}; }' > driver.c
      cc -shared -fPIC -nostdlib -Wl,-soname,libimpurifier-driver.so.1 -o $out/usr/lib/libimpurifier-driver.so.1 driver.c
    '';
  tool =
    pkgs.runCommand "hardware-tool-1"
      {
        pname = "hardware-tool";
        version = "1";
        nativeBuildInputs = [ pkgs.stdenv.cc ];
      }
      ''
        mkdir -p $out/bin
        cat > tool.c <<'SOURCE'
        #include <dlfcn.h>
        #include <stdio.h>
        #include <unistd.h>
        int main(int argc, char **argv) {
          void *driver = dlopen("libimpurifier-driver.so.1", RTLD_NOW);
          if (!driver) { puts(dlerror()); return 1; }
          int (*version)(void) = dlsym(driver, "impurifier_driver_version");
          if (!version) return 2;
          if (argc > 1) {
            FILE *out = fopen("/run/hardware-driver-version", "w");
            if (!out) return 3;
            fprintf(out, "%d\n", version());
            fclose(out);
            for (;;) pause();
          }
          printf("%d\n", version());
          return 0;
        }
        SOURCE
        cc tool.c -ldl -o $out/bin/hardware-tool
      '';
  hardware = {
    libraries."libimpurifier-driver.so.1" = "/usr/lib/libimpurifier-driver.so.1";
    programs = [ "hardware-tool" ];
  };
}
