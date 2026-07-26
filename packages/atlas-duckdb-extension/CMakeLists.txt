cmake_minimum_required(VERSION 3.16...3.29)

if(NOT DEFINED EXTENSION_NAME)
    message(FATAL_ERROR "DuckDB extension name is required")
endif()

if(NOT DEFINED DUCKDB_CAPI_DIR)
    message(FATAL_ERROR "DUCKDB_CAPI_DIR is required; run `make configure` first")
endif()

if(DEFINED TARGET_DUCKDB_VERSION_MAJOR)
    add_compile_definitions(DUCKDB_EXTENSION_API_VERSION_MAJOR=${TARGET_DUCKDB_VERSION_MAJOR})
endif()
if(DEFINED TARGET_DUCKDB_VERSION_MINOR)
    add_compile_definitions(DUCKDB_EXTENSION_API_VERSION_MINOR=${TARGET_DUCKDB_VERSION_MINOR})
endif()
if(DEFINED TARGET_DUCKDB_VERSION_PATCH)
    add_compile_definitions(DUCKDB_EXTENSION_API_VERSION_PATCH=${TARGET_DUCKDB_VERSION_PATCH})
endif()
if(DEFINED DUCKDB_EXTENSION_API_VERSION_UNSTABLE)
    add_compile_definitions(DUCKDB_EXTENSION_API_VERSION_UNSTABLE=${DUCKDB_EXTENSION_API_VERSION_UNSTABLE})
endif()

add_compile_definitions(DUCKDB_EXTENSION_NAME=${EXTENSION_NAME})

project(${EXTENSION_NAME} LANGUAGES C)

add_library(${EXTENSION_NAME} SHARED src/atlas_extension.c)
target_compile_features(${EXTENSION_NAME} PRIVATE c_std_11)
target_include_directories(${EXTENSION_NAME} PRIVATE "${DUCKDB_CAPI_DIR}")

set_target_properties(
    ${EXTENSION_NAME}
    PROPERTIES
        C_VISIBILITY_PRESET hidden
        VISIBILITY_INLINES_HIDDEN ON
)

if(NOT MSVC)
    target_compile_options(${EXTENSION_NAME} PRIVATE -Wall -Wextra)
endif()
