document$.subscribe(function () {
  const mcpButtons = document.querySelectorAll('.md-button--mcp');

  mcpButtons.forEach((button) => {
    button.addEventListener('click', function (e) {
      e.preventDefault();
      showMCPModal();
    });
  });

  function showMCPModal() {
    const modalHtml = `
      <div id="mcp-modal" class="mcp-modal-overlay" role="dialog" aria-labelledby="mcp-title" aria-modal="true">
        <div class="mcp-modal-card">
          <div class="mcp-modal-header">
            <div class="mcp-modal-title-group">
              <div class="mcp-modal-icon">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="url(#mcp-grad)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><defs><linearGradient id="mcp-grad" x1="0" y1="0" x2="100%" y2="100%"><stop offset="0%" stop-color="#4fd1c5"/><stop offset="100%" stop-color="#22d3ee"/></linearGradient></defs><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>
              </div>
              <h3 id="mcp-title">Connect to AI</h3>
            </div>
            <button id="close-mcp" class="close-btn" aria-label="Close modal">
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M1 1L13 13M1 13L13 1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              </svg>
            </button>
          </div>
          
          <div class="mcp-wizard-steps">
            <!-- Step 1: Path -->
            <div class="wizard-step">
              <div class="step-header">
                <span class="step-num">1</span>
                <span class="step-title">Set Project Path</span>
              </div>
              <div class="mcp-path-helper">
                 <div class="input-wrapper">
                   <div class="input-icon">
                     <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>
                   </div>
                   <input type="text" id="mcp-path-input" 
                          class="mcp-path-input"
                          placeholder="/absolute/path/to/baselith-core" 
                          spellcheck="false"
                          aria-label="Absolute path to baselith-core">
                 </div>
              </div>
            </div>

            <!-- Step 2: Client -->
            <div class="wizard-step">
              <div class="step-header">
                <span class="step-num">2</span>
                <span class="step-title">Select AI Client</span>
              </div>
              <div class="mcp-tabs" role="tablist">
                <button class="mcp-tab active" data-tab="standard" role="tab" aria-selected="true">
                  Claude Code
                </button>
                <button class="mcp-tab" data-tab="cursor" role="tab" aria-selected="false">
                  Cursor
                </button>
                <button class="mcp-tab" data-tab="vscode" role="tab" aria-selected="false">
                  VS Code
                </button>
              </div>
            </div>

            <!-- Step 3: Action -->
            <div class="wizard-step step-action">
              <div class="mcp-tab-content active" id="standard-content" role="tabpanel">
                <div class="mcp-instruction">
                  Add to <code>claude_desktop_config.json</code>:
                </div>
                <div class="code-container">
                  <div class="code-header">
                    <span class="code-lang">json</span>
                    <button class="copy-btn-icon" id="copy-standard" aria-label="Copy JSON" title="Copy code">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                    </button>
                  </div>
                  <pre><code id="standard-json">{}</code></pre>
                </div>
              </div>

              <div class="mcp-tab-content" id="cursor-content" role="tabpanel">
                <div class="mcp-instruction">
                  Add as <b>Command Server</b> (stdio):
                </div>
                <div class="code-container">
                  <div class="code-header">
                    <span class="code-lang">bash</span>
                    <button class="copy-btn-icon" id="copy-cursor" aria-label="Copy Command" title="Copy code">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                    </button>
                  </div>
                  <pre><code id="cursor-cmd"></code></pre>
                </div>
              </div>

              <div class="mcp-tab-content" id="vscode-content" role="tabpanel">
                <div class="mcp-instruction">
                  Add to your <b>MCP Client</b> configuration:
                </div>
                <div class="code-container">
                  <div class="code-header">
                    <span class="code-lang">bash</span>
                    <button class="copy-btn-icon" id="copy-vscode" aria-label="Copy Command" title="Copy code">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                    </button>
                  </div>
                  <pre><code id="vscode-cmd"></code></pre>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;

    document.body.insertAdjacentHTML('beforeend', modalHtml);

    const modal = document.getElementById('mcp-modal');
    const pathInput = document.getElementById('mcp-path-input');
    const standardCode = document.getElementById('standard-json');
    const cursorCode = document.getElementById('cursor-cmd');
    const vscodeCode = document.getElementById('vscode-cmd');

    // Auto-fill path if possible
    const savedPath = localStorage.getItem('baselith_docs_path') || '';
    pathInput.value = savedPath;

    setTimeout(() => modal.classList.add('active'), 10);

    function updateConfig() {
      const path = pathInput.value.trim() || '/path/to/baselith-core';
      if (pathInput.value.trim()) {
        localStorage.setItem('baselith_docs_path', pathInput.value.trim());
      }

      const config = {
        mcpServers: {
          'baselith-docs': {
            command: 'python',
            args: ['-m', 'mcp.main'],
            env: { PYTHONPATH: `${path}/mkdocs-site` },
          },
        },
      };

      const jsonStr = JSON.stringify(config, null, 2);
      standardCode.textContent = jsonStr;
      cursorCode.textContent = `export PYTHONPATH=$PYTHONPATH:${path}/mkdocs-site && python -m mcp.main`;
      vscodeCode.textContent = `python -m mcp.main`;
    }

    updateConfig();
    pathInput.addEventListener('input', updateConfig);

    // Tabs logic
    const tabs = modal.querySelectorAll('.mcp-tab');
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        tabs.forEach((t) => {
          t.classList.remove('active');
          t.setAttribute('aria-selected', 'false');
        });
        modal.querySelectorAll('.mcp-tab-content').forEach((c) => c.classList.remove('active'));

        tab.classList.add('active');
        tab.setAttribute('aria-selected', 'true');
        document.getElementById(`${tab.dataset.tab}-content`).classList.add('active');
      });
    });

    // Copy Logic
    const copyToClipboard = (text, btn) => {
      navigator.clipboard.writeText(text).then(() => {
        const originalHTML = btn.innerHTML;

        btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
        btn.classList.add('success');

        setTimeout(() => {
          btn.innerHTML = originalHTML;
          btn.classList.remove('success');
        }, 2000);
      });
    };

    document.getElementById('copy-standard').addEventListener('click', function () {
      copyToClipboard(standardCode.textContent, this);
    });

    document.getElementById('copy-cursor').addEventListener('click', function () {
      copyToClipboard(cursorCode.textContent, this);
    });

    document.getElementById('copy-vscode').addEventListener('click', function () {
      copyToClipboard(vscodeCode.textContent, this);
    });

    // Close logic
    const closeBtn = document.getElementById('close-mcp');
    const closeModal = () => {
      modal.classList.remove('active');
      setTimeout(() => modal.remove(), 300);
    };

    closeBtn.addEventListener('click', closeModal);
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeModal();
    });
  }
});
