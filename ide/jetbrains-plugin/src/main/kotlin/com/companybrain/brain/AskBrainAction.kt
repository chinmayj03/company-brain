package com.companybrain.brain

import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.ui.Messages

/**
 * Placeholder action for the JetBrains plugin scaffold.
 *
 * The real implementation will mirror the VS Code extension: send the
 * selected method or hovered annotation to the harness MCP server (POST /mcp,
 * tool=query_brain) and render the answer in a tool window.
 *
 * For now, this surfaces a notification so reviewers can confirm the plugin
 * loads. Marketplace publishing happens after the VS Code extension proves out.
 */
class AskBrainAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) {
        Messages.showInfoMessage(
            e.project,
            "JetBrains support is coming soon.\n\n" +
                "The VS Code extension ships first (ADR-0052 P7); the JetBrains build " +
                "follows once the wire format and UX are settled.",
            "Company Brain"
        )
    }
}
