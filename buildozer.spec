[app]
title = EchoCoreCB
package.name = echocorecb
package.domain = org.echonexus.agisystem

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 1.0
requirements = python3,kivy,kivymd,plyer,requests,pygithub,openai,google-genai,pyjnius,jnius

[buildozer]
log_level = 2
warn_on_root = 1

# Android specific
android.api = 33
android.minapi = 21
android.ndk = 25b
android.sdk = 33
android.accept_sdk_license = True

android.permissions = INTERNET,ACCESS_NETWORK_STATE,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE

# APK Configuration
android.arch = arm64-v8a,armeabi-v7a
android.allow_backup = True
android.private_storage = True

# Build optimization
android.gradle_dependencies = 
android.java_build_tool = gradle

# Signature
android.debug = 1
