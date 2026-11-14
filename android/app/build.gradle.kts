plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Release signing comes from env (CI secrets). Without it, the debug key is used,
// and each build has a different key, so updates need an uninstall first.
val keystore = System.getenv("TICKLIGHT_KEYSTORE")?.takeIf { file(it).exists() }

android {
    namespace = "app.ticklight.phone"
    compileSdk = 35

    defaultConfig {
        applicationId = "app.ticklight.phone"
        minSdk = 26
        targetSdk = 34
        versionCode = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
        versionName = "2.0." + (System.getenv("GITHUB_RUN_NUMBER") ?: "0")
        val url = (project.findProperty("ticklightUrl") as String?).orEmpty()
        buildConfigField("String", "DEFAULT_URL", "\"$url\"")
    }

    buildFeatures { buildConfig = true }
    lint { checkReleaseBuilds = false }

    signingConfigs {
        if (keystore != null) {
            create("release") {
                storeFile = file(keystore)
                storePassword = System.getenv("TICKLIGHT_KEYSTORE_PASSWORD")
                keyAlias = "ticklight"
                keyPassword = System.getenv("TICKLIGHT_KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
}

dependencies {
    implementation("org.unifiedpush.android:connector:3.3.5")
}
