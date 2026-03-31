project('sentinel-ui', 'vala', 'c',
  version: '1.0.0',
  license: 'MIT',
  meson_version: '>= 0.56.0'
)

# Dependencies
gtk4_dep = dependency('gtk4', version: '>= 4.0')
libadwaita_dep = dependency('libadwaita-1', version: '>= 1.0')
json_glib_dep = dependency('json-glib-1.0')
gstreamer_dep = dependency('gstreamer-1.0')
gstreamer_video_dep = dependency('gstreamer-video-1.0')
gio_dep = dependency('gio-2.0')
gio_unix_dep = dependency('gio-unix-2.0')

# Compile resources (if any)
# resources = gnome.compile_resources(
#   'resources',
#   'src/resources.xml',
#   source_dir: 'src'
# )

# Build executable
executable('sentinel-ui',
  'src/Application.vala',
  'src/MainWindow.vala',
  'src/AuthView.vala',
  'src/EnrollView.vala',
  'src/BackendService.vala',
  'src/CameraPreview.vala',
  'src/IntrusionReviewDialog.vala',
  'src/SettingsView.vala',
  'src/LoginView.vala',
  dependencies: [
    gtk4_dep,
    libadwaita_dep,
    json_glib_dep,
    gstreamer_dep,
    gstreamer_video_dep,
    gio_dep,
    gio_unix_dep
  ],
  install: true
)
