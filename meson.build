# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
project(
  'grabbyhands',
  version: '0.1.0',
  meson_version: '>= 1.1.0',
  license: 'GPL-3.0-or-later',
)

i18n = import('i18n')
gnome = import('gnome')
python = import('python')

app_id = 'io.github.al3phnul.GrabbyHands'
py_installation = python.find_installation('python3')

prefix = get_option('prefix')
pkgdatadir = prefix / get_option('datadir') / meson.project_name()
localedir = prefix / get_option('localedir')

conf = configuration_data()
conf.set('PYTHON', py_installation.full_path())
conf.set('VERSION', meson.project_version())
conf.set('APP_ID', app_id)
conf.set('pkgdatadir', pkgdatadir)
conf.set('localedir', localedir)
conf.set('bindir', prefix / get_option('bindir'))

configure_file(
  input: 'bin/grabbyhands.in',
  output: 'grabbyhands',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

install_subdir(
  'grabbyhands',
  install_dir: pkgdatadir,
  exclude_directories: [
    '__pycache__',
    'editor/__pycache__',
    'editor/tools/__pycache__',
  ],
)

subdir('data')
subdir('po')

gnome.post_install(
  glib_compile_schemas: true,
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)
