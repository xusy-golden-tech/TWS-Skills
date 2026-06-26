cmake_minimum_required(VERSION 3.16)
project(MyProject VERSION 1.0.0 LANGUAGES CXX)

# ------- Options -------------------------------------------------------
option(BUILD_TESTS "Build test targets" ON)
option(BUILD_EXAMPLES "Build example programs" OFF)

# ------- Variables -----------------------------------------------------
set(PROJECT_INCLUDE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/include")
set(PROJECT_SOURCE_DIRS
    src/core
    src/utils
    src/network
)
set(LIBRARY_OUTPUT_PATH ${CMAKE_BINARY_DIR}/lib)
set(EXECUTABLE_OUTPUT_PATH ${CMAKE_BINARY_DIR}/bin)

# ------- Find packages and includes ------------------------------------
find_package(Boost REQUIRED COMPONENTS system filesystem)
find_package(OpenSSL REQUIRED)
find_package(Threads)
include(CTest)
include(GNUInstallDirs)

# ------- Function definitions ------------------------------------------
function(add_test_target target_name test_sources)
    if(BUILD_TESTS)
        add_executable(${target_name} ${test_sources})
        target_link_libraries(${target_name} PRIVATE
            core_lib
            ${Boost_LIBRARIES}
        )
        add_test(NAME ${target_name} COMMAND ${target_name})
    endif()
endfunction()

# ------- Macro definitions ---------------------------------------------
macro(set_common_properties target_name)
    set_target_properties(${target_name} PROPERTIES
        CXX_STANDARD 17
        CXX_STANDARD_REQUIRED ON
        CXX_EXTENSIONS OFF
    )
    target_include_directories(${target_name} PUBLIC
        ${PROJECT_INCLUDE_DIR}
    )
    target_compile_options(${target_name} PRIVATE -Wall -Wextra)
endmacro()

# ------- Targets -------------------------------------------------------
add_library(core_lib STATIC
    src/core/engine.cpp
    src/core/config.cpp
)
set_common_properties(core_lib)

add_library(network_lib SHARED
    src/network/client.cpp
    src/network/server.cpp
)
set_common_properties(network_lib)
target_link_libraries(network_lib PUBLIC
    core_lib
    ${OpenSSL_LIBRARIES}
)

add_executable(my_app
    src/main.cpp
)
set_common_properties(my_app)
target_link_libraries(my_app PRIVATE
    core_lib
    network_lib
)

# ------- Conditional logic ---------------------------------------------
if(BUILD_EXAMPLES)
    add_subdirectory(examples)
endif()

if(WIN32)
    target_compile_definitions(core_lib PRIVATE PLATFORM_WINDOWS)
elseif(APPLE)
    target_compile_definitions(core_lib PRIVATE PLATFORM_APPLE)
else()
    target_compile_definitions(core_lib PRIVATE PLATFORM_LINUX)
endif()

# ------- Loop over subdirectories ---------------------------------------
foreach(subdir IN LISTS PROJECT_SOURCE_DIRS)
    file(GLOB subdir_sources "${subdir}/*.cpp")
    if(subdir_sources)
        message(STATUS "Processing ${subdir}")
    endif()
endforeach()

# ------- Tests ---------------------------------------------------------
if(BUILD_TESTS)
    add_test_target(core_test "src/core/engine_test.cpp;src/core/config_test.cpp")
    add_test_target(network_test "src/network/client_test.cpp;src/network/server_test.cpp")
endif()
