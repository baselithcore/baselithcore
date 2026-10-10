/**
 * Example Widget for Baselith-Core
 * Demonstrates how to extend the UI via plugins.
 */

// Simple React component for the widget
const ExampleWidget = ({ state }) => {
  const [expanded, setExpanded] = React.useState(true);

  return React.createElement(
    'div',
    { className: 'card example-widget' },
    React.createElement(
      'div',
      {
        className: 'card-header',
        onClick: () => setExpanded(!expanded),
        style: {
          cursor: 'pointer',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        },
      },
      React.createElement('h4', { style: { margin: 0 } }, '🔌 Example Plugin'),
      React.createElement(
        'div',
        {
          className: 'example-header-profile',
          style: { display: 'flex', alignItems: 'center', gap: '8px', marginRight: '16px' },
        },
        React.createElement('span', { className: 'role-badge is-admin' }, 'ADMIN'),
        React.createElement('span', { className: 'user-name' }, 'demo_user')
      ),
      React.createElement('span', null, expanded ? '▼' : '▶')
    ),
    expanded &&
      React.createElement(
        'div',
        { className: 'card-body', style: { padding: '1rem' } },
        React.createElement('p', null, 'This widget is injected dynamically!'),
        React.createElement(
          'div',
          { className: 'status-row' },
          React.createElement('span', { className: 'meta-label' }, 'Status'),
          React.createElement(
            'span',
            { className: 'status-chip ok live' },
            React.createElement('span', { className: 'dot' }),
            ' Active'
          )
        )
      )
  );
};

// Plugin initialization hook
window.examplePlugin = {
  initialize: (registry) => {
    console.log('🔌 Example Plugin initializing...');

    // Register the widget for the chat sidebar
    registry.registerWidget('chat-sidebar', 'example-widget', ExampleWidget);

    console.log('✅ Example Plugin initialized');
  },
};

// Auto-register if the registry is already available (or wait for loader)
if (window.pluginRegistry) {
  window.examplePlugin.initialize(window.pluginRegistry);
}
