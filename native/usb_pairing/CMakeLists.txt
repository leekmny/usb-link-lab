cmake_minimum_required(VERSION 3.24)
project(USBLinkLabPairing LANGUAGES C)

include(FetchContent)
set(ENABLE_PROGRAMS OFF CACHE BOOL "" FORCE)
set(ENABLE_TESTING OFF CACHE BOOL "" FORCE)
set(USE_SHARED_MBEDTLS_LIBRARY OFF CACHE BOOL "" FORCE)
set(USE_STATIC_MBEDTLS_LIBRARY ON CACHE BOOL "" FORCE)
set(MBEDTLS_FATAL_WARNINGS OFF CACHE BOOL "" FORCE)

include_directories("${CMAKE_CURRENT_SOURCE_DIR}")
add_compile_definitions(MBEDTLS_CONFIG_FILE="ulab_mbedtls_config.h")
FetchContent_Declare(
    mbedtls
    URL https://github.com/Mbed-TLS/mbedtls/releases/download/mbedtls-3.6.7/mbedtls-3.6.7.tar.bz2
    URL_HASH SHA256=a7e8bcbec0e6f761b4af24f25677626b35f762f68eef79c08677a363212d11f6
    DOWNLOAD_EXTRACT_TIMESTAMP FALSE
)
FetchContent_MakeAvailable(mbedtls)

if(WIN32)
    add_library(ulab_pairing SHARED ulab_pairing.c)
    target_compile_definitions(ulab_pairing PRIVATE ULAB_PAIRING_EXPORTS)
    set_target_properties(ulab_pairing PROPERTIES
        PREFIX ""
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/bin/$<CONFIG>"
    )
else()
    add_library(ulab_pairing STATIC ulab_pairing.c)
endif()
target_compile_features(ulab_pairing PRIVATE c_std_99)
target_link_libraries(ulab_pairing PRIVATE mbedcrypto)
if(MINGW)
    target_link_options(ulab_pairing PRIVATE -static-libgcc)
endif()

if(CMAKE_SYSTEM_NAME STREQUAL "iOS")
    set(complete_archive "${CMAKE_CURRENT_BINARY_DIR}/libulab_pairing_complete.a")
    add_custom_command(TARGET ulab_pairing POST_BUILD
        COMMAND xcrun libtool -static -o "${complete_archive}"
            "$<TARGET_FILE:ulab_pairing>" "$<TARGET_FILE:mbedcrypto>"
        BYPRODUCTS "${complete_archive}"
        VERBATIM
    )
endif()
