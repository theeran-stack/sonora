/**
 * SONORA Global AI Assistant Widget Client.
 * Responsive, accessible, and grounded in actual SQLite FAQ and ErrorCode models.
 */

(function () {
  "use strict";

  const root = document.getElementById("sonoraAssistantRoot");
  if (!root) return;

  const launcher = document.getElementById("assistantLauncher");
  const drawer = document.getElementById("assistantDrawer");
  const closeBtn = document.getElementById("assistantCloseBtn");
  const clearBtn = document.getElementById("assistantClearBtn");
  const messagesContainer = document.getElementById("assistantMessages");
  const form = document.getElementById("assistantForm");
  const input = document.getElementById("assistantInput");
  const chipsContainer = document.getElementById("assistantChips");

  // Persistent session identifier for chat history
  let sessionId = localStorage.getItem("sonora_assistant_session_id");
  if (!sessionId) {
    sessionId = "session_" + Math.random().toString(36).substring(2, 12);
    localStorage.setItem("sonora_assistant_session_id", sessionId);
  }

  let isOpen = false;

  function toggleAssistant(open) {
    isOpen = typeof open === "boolean" ? open : !isOpen;
    if (isOpen) {
      drawer.classList.add("open");
      drawer.removeAttribute("inert");
      launcher.classList.add("active");
      setTimeout(() => input.focus(), 150);
    } else {
      drawer.classList.remove("open");
      drawer.setAttribute("inert", "");
      launcher.classList.remove("active");
    }
  }

  launcher.addEventListener("click", () => toggleAssistant());
  closeBtn.addEventListener("click", () => toggleAssistant(false));

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isOpen) {
      toggleAssistant(false);
    }
  });

  // Simple safe markdown parser (bold, code blocks, links, bullets)
  function renderMarkdown(text) {
    if (!text) return "";
    let safe = text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");

    // Headings
    safe = safe.replace(/^### (.*$)/gim, '<h4 class="msg-h4">$1</h4>');
    safe = safe.replace(/^## (.*$)/gim, '<h3 class="msg-h3">$1</h3>');

    // Bold
    safe = safe.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
    // Italics
    safe = safe.replace(/\*(.*?)\*/g, "<em>$1</em>");
    // Inline code
    safe = safe.replace(/`([^`]+)`/g, '<code class="msg-code">$1</code>');
    // Links
    safe = safe.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" class="msg-link" target="_self">$1</a>');
    // Blockquotes
    safe = safe.replace(/^&gt; (.*$)/gim, '<blockquote class="msg-quote">$1</blockquote>');
    // Bullet points
    safe = safe.replace(/^\s*-\s+(.*$)/gim, '<li class="msg-li">$1</li>');
    safe = safe.replace(/(<li class="msg-li">.*<\/li>)/gims, '<ul class="msg-ul">$1</ul>');

    // Paragraph breaks
    return safe.replace(/\n\n+/g, "<br><br>").replace(/\n/g, "<br>");
  }

  function appendMessage(role, rawContent, sources = [], messageId = "") {
    const msgDiv = document.createElement("div");
    msgDiv.className = `msg msg-${role}`;

    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";

    if (role === "assistant") {
      bubble.innerHTML = renderMarkdown(rawContent);

      // Render source citations if present
      if (sources && sources.length > 0) {
        const sourcesDiv = document.createElement("div");
        sourcesDiv.className = "msg-sources";
        sources.forEach((s) => {
          const pill = document.createElement("span");
          pill.className = "source-pill";
          if (s.type === "error_code") {
            pill.innerHTML = `🛡️ Error: <strong>${s.code}</strong> (HTTP ${s.http_status})`;
          } else if (s.type === "faq") {
            pill.innerHTML = `📖 FAQ: <strong>${s.category}</strong>`;
          } else {
            pill.innerHTML = `⚙️ ${s.title || "System Verified"}`;
          }
          sourcesDiv.appendChild(pill);
        });
        bubble.appendChild(sourcesDiv);
      }

      // Render feedback rating bar
      const feedbackBar = document.createElement("div");
      feedbackBar.className = "msg-feedback";
      feedbackBar.innerHTML = `
        <span class="fb-prompt">Was this helpful?</span>
        <button class="fb-btn" data-rating="5" title="Helpful" aria-label="Helpful">👍</button>
        <button class="fb-btn" data-rating="1" title="Not helpful" aria-label="Not helpful">👎</button>
      `;

      feedbackBar.querySelectorAll(".fb-btn").forEach((btn) => {
        btn.addEventListener("click", function () {
          const rating = parseInt(this.getAttribute("data-rating"), 10);
          sendFeedback(rating, rawContent, messageId, feedbackBar);
        });
      });

      bubble.appendChild(feedbackBar);
    } else {
      bubble.textContent = rawContent;
    }

    const timeSpan = document.createElement("span");
    timeSpan.className = "msg-time";
    const now = new Date();
    timeSpan.textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

    msgDiv.appendChild(bubble);
    msgDiv.appendChild(timeSpan);
    messagesContainer.appendChild(msgDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    return msgDiv;
  }

  function appendTypingIndicator() {
    const typing = document.createElement("div");
    typing.className = "msg msg-assistant typing-indicator";
    typing.id = "assistantTyping";
    typing.innerHTML = `
      <div class="msg-bubble">
        <span class="dot"></span><span class="dot"></span><span class="dot"></span>
      </div>
    `;
    messagesContainer.appendChild(typing);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    return typing;
  }

  function removeTypingIndicator() {
    const el = document.getElementById("assistantTyping");
    if (el) el.remove();
  }

  async function sendMessage(text) {
    const trimmed = (text || input.value || "").trim();
    if (!trimmed) return;

    appendMessage("user", trimmed);
    input.value = "";
    const typing = appendTypingIndicator();

    try {
      const resp = await fetch("/api/assistant/chat/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: trimmed, session_id: sessionId }),
      });

      removeTypingIndicator();

      if (!resp.ok) {
        throw new Error(`Server returned status ${resp.status}`);
      }

      const data = await resp.json();
      appendMessage("assistant", data.reply, data.sources || [], data.session_id);
    } catch (err) {
      removeTypingIndicator();
      appendMessage(
        "assistant",
        `**Connection Notice**: Unable to contact assistant endpoint (${err.message}). You can still browse all topics on the [FAQ Page](/faq/) or [Error Codes Catalogue](/errors/).`
      );
    }
  }

  async function sendFeedback(rating, replyText, messageId, container) {
    try {
      container.innerHTML = `<span class="fb-thanks">✓ Thank you for your feedback!</span>`;
      await fetch("/api/assistant/feedback/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          rating: rating,
          comments: rating === 5 ? "Positive rating" : "Negative rating",
          message_id: messageId,
        }),
      });
    } catch (e) {
      console.warn("Feedback submission error:", e);
    }
  }

  // Handle form submit
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage();
  });

  // Handle suggestion chips
  if (chipsContainer) {
    chipsContainer.addEventListener("click", (e) => {
      const btn = e.target.closest(".chip-btn");
      if (btn) {
        const prompt = btn.getAttribute("data-prompt");
        if (prompt) {
          sendMessage(prompt);
        }
      }
    });
  }

  // Clear chat
  clearBtn.addEventListener("click", () => {
    messagesContainer.innerHTML = `
      <div class="msg msg-assistant">
        <div class="msg-bubble">
          <p>Conversation history reset. Ask me any question about SONORA's architecture, audio streaming, or error recovery!</p>
        </div>
        <span class="msg-time">Just now</span>
      </div>
    `;
  });

  // Global helper accessible from anywhere on any page
  window.SonoraAssistant = {
    open: () => toggleAssistant(true),
    close: () => toggleAssistant(false),
    toggle: () => toggleAssistant(),
    openWithPrompt: (prompt) => {
      toggleAssistant(true);
      if (prompt) {
        input.value = prompt;
        sendMessage(prompt);
      }
    },
  };
})();

