cmake_minimum_required(VERSION 3.20)
# The single source of the version: the programs and the Windows installer
# (installer/windows/build-installer.ps1) take it from here.
project(QuiltPatternViewer VERSION 1.1.0 LANGUAGES C CXX)

option(QPV_BUILD_GUI "Build the Qt 6 desktop viewer when Qt is available" ON)
option(QPV_BUILD_TESTS "Build the dependency-free unit tests" ON)
option(QPV_BUILD_KEYGEN "Build qpv-keygen, the license key generator (never installed)" ON)
# Also settable in include/qpv/license_config.hpp; see README.md, "Licensing".
option(QPV_LICENSING "Require a license key for the paid features after the trial" OFF)
set(QPV_LICENSE_PUBLIC_KEY_HEADER "" CACHE FILEPATH
    "Public key header to build with instead of include/qpv/license_public_key.hpp")

if(QPV_BUILD_TESTS)
    enable_testing()
endif()

if(MSVC)
    set(qpv_warnings /W4 /permissive- /utf-8)
else()
    set(qpv_warnings -Wall -Wextra -Wpedantic)
endif()

# License key signing and checking (Monocypher's EdDSA), shared by the
# programs and qpv-keygen.
add_library(qpv_license_key STATIC
    src/license_key.cpp
    third_party/monocypher/monocypher.c
)
target_include_directories(qpv_license_key PUBLIC include third_party/monocypher)
target_compile_features(qpv_license_key PUBLIC cxx_std_20)
target_compile_options(qpv_license_key PRIVATE $<$<COMPILE_LANGUAGE:CXX>:${qpv_warnings}>)

add_library(qpv_core
    src/parse_double.cpp
    src/pattern_io.cpp
    src/qli.cpp
    src/svg.cpp
    src/license.cpp
)
target_include_directories(qpv_core PUBLIC include)
target_compile_features(qpv_core PUBLIC cxx_std_20)
target_compile_definitions(qpv_core PUBLIC QPV_VERSION="${PROJECT_VERSION}")
target_compile_options(qpv_core PRIVATE ${qpv_warnings})
target_link_libraries(qpv_core PUBLIC qpv_license_key)
if(QPV_LICENSING)
    target_compile_definitions(qpv_core PUBLIC QPV_LICENSING=1)
endif()
if(QPV_LICENSE_PUBLIC_KEY_HEADER)
    file(TO_CMAKE_PATH "${QPV_LICENSE_PUBLIC_KEY_HEADER}" qpv_key_header)
    target_compile_definitions(qpv_core PRIVATE QPV_LICENSE_PUBLIC_KEY_HEADER="${qpv_key_header}")
endif()

add_executable(qpv-cli src/cli_main.cpp)
target_link_libraries(qpv-cli PRIVATE qpv_core)
if(WIN32)
    # CommandLineToArgvW, for file names outside the ANSI code page.
    target_link_libraries(qpv-cli PRIVATE shell32)
endif()

if(QPV_BUILD_KEYGEN)
    # Only qpv_license_key, so it builds before a real public key exists.
    add_executable(qpv-keygen src/keygen_main.cpp)
    target_link_libraries(qpv-keygen PRIVATE qpv_license_key)
    target_compile_options(qpv-keygen PRIVATE ${qpv_warnings})
    if(WIN32)
        target_link_libraries(qpv-keygen PRIVATE shell32 bcrypt)
    endif()
endif()

if(QPV_BUILD_GUI)
    find_package(Qt6 6.4 QUIET COMPONENTS Widgets)
    if(Qt6_FOUND)
        qt_standard_project_setup()
        qt_add_executable(qpv
            src/gui_main.cpp
            src/app_window.cpp
            src/license_dialog.cpp
            src/pattern_canvas.cpp
            include/qpv/license_dialog.hpp
            include/qpv/main_window.hpp
            include/qpv/pattern_canvas.hpp
        )
        target_link_libraries(qpv PRIVATE qpv_core Qt6::Widgets)
        set_target_properties(qpv PROPERTIES
            WIN32_EXECUTABLE TRUE
            MACOSX_BUNDLE TRUE
        )
        if(QPV_BUILD_TESTS)
            qt_add_executable(qpv-thumbnail-tests
                tests/thumbnail_tests.cpp
                src/app_window.cpp
                src/license_dialog.cpp
                src/pattern_canvas.cpp
                include/qpv/license_dialog.hpp
                include/qpv/main_window.hpp
                include/qpv/pattern_canvas.hpp
            )
            target_link_libraries(qpv-thumbnail-tests PRIVATE qpv_core Qt6::Widgets)
            set_target_properties(qpv-thumbnail-tests PROPERTIES WIN32_EXECUTABLE FALSE)
            add_test(NAME qpv-thumbnail-tests COMMAND qpv-thumbnail-tests)
            set_tests_properties(qpv-thumbnail-tests PROPERTIES TIMEOUT 20)
            if(TARGET Qt6::QOffscreenIntegrationPlugin)
                set_property(TEST qpv-thumbnail-tests PROPERTY ENVIRONMENT
                    "QT_QPA_PLATFORM=offscreen"
                    "QT_QPA_PLATFORM_PLUGIN_PATH=$<TARGET_FILE_DIR:Qt6::QOffscreenIntegrationPlugin>"
                )
            else()
                set_property(TEST qpv-thumbnail-tests PROPERTY ENVIRONMENT
                    "QT_QPA_PLATFORM=offscreen")
            endif()
        endif()
        install(TARGETS qpv qpv-cli
            BUNDLE DESTINATION .
            RUNTIME DESTINATION bin)
    else()
        message(STATUS "Qt 6 Widgets not found: building qpv-cli only")
        install(TARGETS qpv-cli RUNTIME DESTINATION bin)
    endif()
else()
    install(TARGETS qpv-cli RUNTIME DESTINATION bin)
endif()

if(QPV_BUILD_TESTS)
    add_executable(qpv-tests tests/core_tests.cpp)
    target_link_libraries(qpv-tests PRIVATE qpv_core)
    add_test(NAME qpv-core-tests COMMAND qpv-tests)
    add_executable(qpv-license-tests tests/license_tests.cpp)
    target_link_libraries(qpv-license-tests PRIVATE qpv_core)
    add_test(NAME qpv-license-tests COMMAND qpv-license-tests)
endif()
