.PHONY: all configure debug release test test_debug test_release clean clean_all headers

PROJ_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

EXTENSION_NAME := atlas
PYTHON_BIN ?= python3.14

# Stable C API is the default. Enabling the unstable API pins the binary to the
# exact target DuckDB version and is reserved for explicit capability probes.
USE_UNSTABLE_C_API ?= 0
# This is the stable C-extension ABI baseline, not the DuckDB test runtime.
# DuckDB 1.5.x accepts stable C API extensions targeting v1.2.0 and lower.
TARGET_DUCKDB_VERSION ?= v1.2.0
DUCKDB_TEST_VERSION ?= 1.5.4

DUCKDB_CAPI_DIR := $(PROJ_DIR)configure/duckdb_capi
CMAKE_EXTRA_BUILD_FLAGS += -DDUCKDB_CAPI_DIR=$(DUCKDB_CAPI_DIR)

all: configure release

include extension-ci-tools/makefiles/c_api_extensions/base.Makefile
include extension-ci-tools/makefiles/c_api_extensions/c_cpp.Makefile

configure: venv platform extension_version headers

headers: $(DUCKDB_CAPI_DIR)/duckdb.h $(DUCKDB_CAPI_DIR)/duckdb_extension.h

$(DUCKDB_CAPI_DIR)/duckdb.h:
	@mkdir -p "$(DUCKDB_CAPI_DIR)"
	$(PYTHON_BIN) -c "import urllib.request; urllib.request.urlretrieve('https://raw.githubusercontent.com/duckdb/duckdb/$(TARGET_DUCKDB_VERSION)/src/include/duckdb.h', '$@')"

$(DUCKDB_CAPI_DIR)/duckdb_extension.h:
	@mkdir -p "$(DUCKDB_CAPI_DIR)"
	$(PYTHON_BIN) -c "import urllib.request; urllib.request.urlretrieve('https://raw.githubusercontent.com/duckdb/duckdb/$(TARGET_DUCKDB_VERSION)/src/include/duckdb_extension.h', '$@')"

debug: build_extension_library_debug build_extension_with_metadata_debug
release: build_extension_library_release build_extension_with_metadata_release

test: test_debug
test_debug: test_extension_debug
test_release: test_extension_release

clean: clean_build clean_cmake
clean_all: clean clean_configure
