[app]
# The name and package information for your application.
title = EchoCoreCB
package.name = echocorecb
package.domain = org.echonexus.agisystem

# The directory where your application source code is located.
source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 1.0

# The list of Python libraries and dependencies your app needs.
# The 'google-genai' library has been removed as it is not compatible with the Android build toolchain.
requirements = python3,kivy,kivymd,plyer,requests,pygithub,openai,pyjnius,jnius

[buildozer]
# Sets the level of detail for the build log. Higher is more detailed.
log_level = 2
warn_on_root = 1

# Android specific configurations
# It's good practice to explicitly set these for consistent builds.
android.api = 33
android.minapi = 21
android.ndk = 25b
android.sdk = 33

# This is a crucial setting that automatically accepts the Android SDK license,
# which is necessary for automated builds in a CI/CD environment.
android.accept_sdk_license = True

# Specifies permissions your app needs on the device.
android.permissions = INTERNET,ACCESS_NETWORK_STATE,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE

# APK Configuration
# Specifies the target CPU architectures for your APK.
android.arch = arm64-v8a,armeabi-v7a
android.allow_backup = True
android.private_storage = True

# Build optimization
android.gradle_dependencies = 
android.java_build_tool = gradle

# Signature
android.debug = 1
