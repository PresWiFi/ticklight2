plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "app.ticklight.watch"
    compileSdk = 35

    defaultConfig {
        applicationId = "app.ticklight.watch"
        minSdk = 30          // Wear OS 3+ (Galaxy Watch4 and newer)
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
        val url = (project.findProperty("ticklightUrl") as String?).orEmpty()
        buildConfigField("String", "DEFAULT_URL", "\"$url\"")
    }

    buildFeatures { buildConfig = true }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}
