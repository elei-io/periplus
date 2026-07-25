#include "duckdb_extension.h"

DUCKDB_EXTENSION_EXTERN

static void AtlasExtensionApi(duckdb_function_info info,
                              duckdb_data_chunk input, duckdb_vector output) {
  idx_t row_count = duckdb_data_chunk_get_size(input);

  (void)info;

  for (idx_t row = 0; row < row_count; row++) {
    duckdb_vector_assign_string_element(output, row, "stable-c-api");
  }
}

static bool RegisterAtlasExtensionApi(duckdb_connection connection) {
  duckdb_scalar_function function = duckdb_create_scalar_function();
  duckdb_logical_type return_type =
      duckdb_create_logical_type(DUCKDB_TYPE_VARCHAR);

  duckdb_scalar_function_set_name(function, "atlas_extension_api");
  duckdb_scalar_function_set_return_type(function, return_type);
  duckdb_scalar_function_set_function(function, AtlasExtensionApi);

  duckdb_state state = duckdb_register_scalar_function(connection, function);

  duckdb_destroy_logical_type(&return_type);
  duckdb_destroy_scalar_function(&function);

  return state == DuckDBSuccess;
}

DUCKDB_EXTENSION_ENTRYPOINT(duckdb_connection connection,
                            duckdb_extension_info info,
                            struct duckdb_extension_access *access) {
  (void)info;
  (void)access;

  return RegisterAtlasExtensionApi(connection);
}
