# Video converter tool

Video converter tool is for converting video files with ffmpeg to more compact and different file formats such as `.mp4`.

The GUI app's source code is located in the `gui-app` folder. The app can be built and packaged into a single executable on Windows, Linux and MacOS with the following steps:

1. `python -m pip install -r requirements-build.txt`
2. `python scripts/build.py --ffmpeg-dir /path/to/ffmpeg/bin^
3. Your built app is located in the `gui-app/dist` folder

