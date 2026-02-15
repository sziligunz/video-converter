#include <filesystem>
#include <iostream>
#include <cstdlib>

namespace fs = std::filesystem;

int convertMkvToMp4(const fs::path& folderPath) {
    if (!folderPath.is_absolute()) {
        throw std::runtime_error("Path must be absolute!");
    }

    if (!fs::exists(folderPath) || !fs::is_directory(folderPath)) {
        throw std::runtime_error("Folder does not exist!");
    }

    int numberOfConversions = 0;

    for (const auto& entry : fs::directory_iterator(folderPath)) {
        if (!entry.is_regular_file()) {
            continue;
        }

        const fs::path& inputPath = entry.path();

        if (inputPath.extension() != ".mkv") {
            continue;
        }

        fs::path outputPath = inputPath;
        outputPath.replace_extension(".mp4");

        if (fs::exists(outputPath)) {
            std::cout << "Skipping (already exists): " << outputPath.filename() << '\n';
            continue;
        }
        std::string command =

            "ffmpeg -i \"" + inputPath.string() + "\" -c:v libx265 -preset medium -crf 23 -c:a copy \"" +
            outputPath.string() + "\"";

        std::cout << "Converting: " << inputPath.filename() << std::endl;

        int result = std::system(command.c_str());
        if (result != 0) {
            std::cerr << "ffmpeg failed for " << inputPath.filename() << std::endl;
        } else {
            numberOfConversions++;
        }
    }

    return numberOfConversions;
}


int main(int argc, const char* argv[]) {
    try {
        if (argc != 2) {
            throw std::runtime_error("Argument of folder's abolute path is missing.");
        }
        int numberOfConversions = convertMkvToMp4(argv[1]);
        std::cout << "Number of conversions: " << numberOfConversions << std::endl;
    } catch (std::runtime_error& error) {
        std::cout << "Error: " << error.what() << std::endl;
    }
    return 0;
}