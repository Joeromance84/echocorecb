[app]

# (str) Title of your application
title = Access Node

# (str) Package name. It should be unique on Google Play.
package.name = org.lorentz.accessnode

# (str) Package domain (needed for Android/iOS packaging)
package.domain = com.developer.tower

# (str) Source code where the main.py lives.
source.dir = .

# (list) Source files to exclude (directories are excluded by default)
source.exclude_dirs =
    tests
    bin
    tower_resources

# (list) Application requirements
# We must specify all required packages here. Use a minimal set.
requirements = 
    python3==3.11.9
    fastapi==0.103.2
    uvicorn==0.23.2
    starlette==0.27.0
    websockets==11.0.3
    pydantic==1.10.13
    sqlite3==3.44.2
    RPyC==5.3.0

# (bool) If your application requires Android internet permission
android.permissions = 
    android.permission.INTERNET

# (int) Minimum API level for Android
android.minapi = 21

# (int) Target API level for Android
android.targetapi = 34

# (bool) Whether to create a background service
android.logcat_filters = *:S python:D
android.service_class = org.lorentz.accessnode.ServiceStarter

# (list) Extra arguments to pass to python-for-android
# This is a key part of our size optimization. We explicitly blacklist
# unnecessary standard library modules to reduce the size of the Python bundle.
p4a.extra_args = --exclude-modules "tkinter,test,distutils,ensurepip,unittest,xml.etree,asyncio.test"

# (bool) Set to True to enable debug mode for logcat messages
android.debug = True

# (str) Supported architectures
android.archs = armeabi-v7a, arm64-v8a

# (str) Android NDK version. We'll use a recent, stable version.
android.ndk = 26b

# (str) Android SDK version.
android.sdk = 34
