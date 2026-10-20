cmake_minimum_required(VERSION 3.20)
project(QuiltPatternViewer VERSION 1.0.0 LANGUAGES CXX)

option(QPV_BUILD_GUI "Build the Qt 6 desktop viewer when Qt is available" ON)
option(QPV_BUILD_TESTS "Build the dependency-free unit tests" ON)

if(QPV_BUILD_TESTS)
    enable_testing()
endif()

add_library(qpv_core
    src/pattern_io.cpp
    src/qli.cpp
    src/svg.cpp
)
target_include_directories(qpv_core PUBLIC include)
target_compile_features(qpv_core PUBLIC cxx_std_20)
if(MSVC)
    target_compile_options(qpv_core PRIVATE /W4 /permissive- /utf-8)
else()
    target_compile_options(qpv_core PRIVATE -Wall -Wextra -Wpedantic)
endif()

add_executable(qpv-cli src/cli_main.cpp)
target_link_libraries(qpv-cli PRIVATE qpv_core)

if(QPV_BUILD_GUI)
    find_package(Qt6 6.4 QUIET COMPONENTS Widgets)
    if(Qt6_FOUND)
        qt_standard_project_setup()
        qt_add_executable(qpv
            src/gui_main.cpp
            src/app_window.cpp
            src/pattern_canvas.cpp
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
                src/pattern_canvas.cpp
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
endif()
