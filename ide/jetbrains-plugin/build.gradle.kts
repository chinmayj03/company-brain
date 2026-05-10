// JetBrains plugin scaffold for company-brain (ADR-0052 P7).
//
// This is intentionally a skeleton. Marketplace publishing is deferred until
// the VS Code version is battle-tested; for now `./gradlew buildPlugin` just
// produces a runnable empty plugin that registers a single placeholder action.

plugins {
    id("org.jetbrains.intellij") version "1.17.3"
    kotlin("jvm") version "1.9.22"
}

group = "com.companybrain"
version = "0.1.0"

repositories {
    mavenCentral()
}

intellij {
    version.set("2024.1")
    type.set("IC")
    plugins.set(listOf("com.intellij.java"))
}

tasks {
    patchPluginXml {
        sinceBuild.set("241")
        untilBuild.set("251.*")
    }
    runIde {
        autoReloadPlugins.set(true)
    }
}
