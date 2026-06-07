cmake_minimum_required(VERSION 4.2.3)

project(video-converter)

set(CMAKE_CXX_STANDARD 17)

set(SOURCE_FILES main.cpp)

add_executable(video-converter ${SOURCE_FILES})
