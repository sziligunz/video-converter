#include <filesystem>
#include <iostream>
#include <cstdlib>

namespace fs = std::filesystem;

bool runFfmpegConversion(std::__1::__fs::filesystem::directory_entry target, const char* mode) {
    if (!target.is_regular_file()) {
        return false;
    }

    const fs::path& inputPath = target.path();

    if (inputPath.extension() != ".mkv") {
        return false;
    }

    fs::path outputPath = inputPath;
    outputPath.replace_extension(".mp4");

    if (fs::exists(outputPath)) {
        std::cout << "Skipping (already exists): " << outputPath.filename() << '\n';
        return false;
    }
    std::string command = "";
    if (mode == nullptr) {
        command = "ffmpeg -i \"" + inputPath.string() + "\" -c:v hevc_videotoolbox -preset medium -crf 23 -c:a copy \"" +
        outputPath.string() + "\"";
    }        
    else if (strcmp(mode, "compact") == 0) {
        command = "ffmpeg -i \"" + inputPath.string() + "\" -c:v hevc_videotoolbox -preset medium -crf 23 -b:v 4000k -c:a copy \"" +
        outputPath.string() + "\"";
    }

    std::cout << "Converting: " << inputPath.filename() << std::endl;

    int result = std::system(command.c_str());
    if (result != 0) {
        std::cerr << "ffmpeg failed for " << inputPath.filename() << std::endl;
        return false;
    } else {
        return true;
    }
}

int convertMkvToMp4(const fs::path& folderPath, const char* mode) {
    if (!fs::exists(folderPath) && !fs::is_directory(folderPath)) {
        throw std::runtime_error("Folder does not exist!");
    }
    if (!fs::exists(folderPath) && !fs::is_regular_file(folderPath)) {
        throw std::runtime_error("File does not exist!");
    }

    int numberOfConversions = 0;
    
    if (fs::is_regular_file(folderPath)) {
        if (runFfmpegConversion(std::__1::__fs::filesystem::directory_entry(folderPath), mode)) numberOfConversions++;
    }
    else {
        for (const auto& entry : fs::directory_iterator(folderPath)) {
            if (runFfmpegConversion(entry, mode)) numberOfConversions++;
        }
    }

    return numberOfConversions;
}


int main(int argc, const char* argv[]) {
    try {
        if (argc < 2) {
            throw std::runtime_error("Target path is missing.");
        }
        int numberOfConversions = 0;
        if (argc == 2) {
            numberOfConversions = convertMkvToMp4(argv[1], nullptr);    
        }
        else {
            numberOfConversions = convertMkvToMp4(argv[1], argv[2]);
        } 
        std::cout << "Number of conversions: " << numberOfConversions << std::endl;
    } catch (std::runtime_error& error) {
        std::cout << "Error: " << error.what() << std::endl;
    }
    return 0;
}
