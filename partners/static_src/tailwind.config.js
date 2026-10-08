module.exports = {
    content: [
        './partners/templates/partners/offline/**/*.html',
        './partners/static/partners/offline-app.js',
    ],
    theme: {
        extend: {
            fontFamily: {
                sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
                display: ['Poppins', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
            },
        },
    },
    plugins: [],
};
