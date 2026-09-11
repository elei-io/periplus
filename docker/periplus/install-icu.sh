#!/bin/sh
# Shared native tokenizer build for the runtime image and backend CI.
set -eu
icu_build_dir=$(mktemp -d)
trap 'rm -rf "$icu_build_dir"' EXIT
cd "$icu_build_dir"
curl --fail --location --retry 3 --output icu.tgz \
  https://github.com/unicode-org/icu/releases/download/release-77-1/icu4c-77_1-src.tgz
printf '%s  %s\n' \
  a47d6d9c327d037a05ea43d1d1a06b2fd757cc02a94f7c1a238f35cfc3dfd4ab78d0612790f3a3cca0292c77412a9c2c15c8f24b718f79a857e007e66f07e7cd \
  icu.tgz | sha512sum --check -
tar -xzf icu.tgz
cd icu/source
./configure --prefix=/usr/local --disable-tests --disable-samples
make -j2
make install
install -Dm644 ../LICENSE /usr/local/share/licenses/icu/LICENSE
ldconfig
