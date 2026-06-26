# Helper CMake module with utility functions and macros
# This file is included via include() from CMakeLists.txt or similar

# ------- Functions -----------------------------------------------------
function(download_file url destination)
    message(STATUS "Downloading ${url} to ${destination}")
    file(DOWNLOAD ${url} ${destination}
        STATUS download_status
        SHOW_PROGRESS
    )
    set(STATUS_OK TRUE)
    return()
endfunction()

function(extract_archive archive_path output_dir)
    file(MAKE_DIRECTORY ${output_dir})
    execute_process(
        COMMAND ${CMAKE_COMMAND} -E tar xzf ${archive_path}
        WORKING_DIRECTORY ${output_dir}
        RESULT_VARIABLE extract_result
    )
    set(EXTRACT_OK TRUE PARENT_SCOPE)
endfunction()

function(configure_helper target_name config_file)
    configure_file(
        ${config_file}
        ${CMAKE_CURRENT_BINARY_DIR}/${target_name}/config.h
    )
    target_include_directories(${target_name} PRIVATE
        ${CMAKE_CURRENT_BINARY_DIR}/${target_name}
    )
endfunction()

# ------- Macros --------------------------------------------------------
macro(add_benchmark bench_name bench_source)
    add_executable(${bench_name} ${bench_source})
    target_link_libraries(${bench_name} PRIVATE
        core_lib
        benchmark::benchmark
    )
endmacro()

macro(set_warning_level target_name level)
    if(MSVC)
        target_compile_options(${target_name} PRIVATE /W${level})
    else()
        target_compile_options(${target_name} PRIVATE -W${level})
    endif()
endmacro()

# ------- Variables ------------------------------------------------------
set(HELPER_VERSION "2.1.0")
set(HELPER_CACHE_DIR "${CMAKE_BINARY_DIR}/cache")
option(HELPER_VERBOSE "Enable verbose helper output" OFF)

# ------- Resource file generation ---------------------------------------
set(RESOURCE_LIST
    resource_a
    resource_b
    resource_c
)

foreach(res IN LISTS RESOURCE_LIST)
    message(STATUS "Registering resource: ${res}")
endforeach()
