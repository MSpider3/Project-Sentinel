/* MainWindow.vala - Main application window with tab navigation */

namespace Sentinel {

    public class MainWindow : Gtk.ApplicationWindow {
        private BackendService backend;
        private Gtk.Stack stack;
        private AuthView auth_view;
        private EnrollView enroll_view;
        private SettingsView settings_view;
        private LoginView login_view;
        private Gtk.Overlay overlay;

        public MainWindow (Gtk.Application app) {
            Object (application: app);

            title = "Project Sentinel";
            default_width = 1000;
            default_height = 700;

            backend = new BackendService ();
            backend.error_occurred.connect (on_backend_error);

            setup_ui ();
            initialize_backend.begin ();
        }

        private void setup_ui () {
            // Header bar
            var header = new Gtk.HeaderBar ();
            set_titlebar (header);

            // Stack switcher
            var stack_switcher = new Gtk.StackSwitcher ();
            stack_switcher.halign = Gtk.Align.CENTER;
            header.set_title_widget (stack_switcher);

            // Main stack
            stack = new Gtk.Stack ();
            stack.vexpand = true;
            stack.hexpand = true;
            stack.transition_type = Gtk.StackTransitionType.SLIDE_LEFT_RIGHT;
            stack_switcher.stack = stack;

            // 1. Authentication view
            auth_view = new AuthView (backend);
            stack.add_titled (auth_view, "auth", "Authenticate");

            // 2. Enrollment view
            enroll_view = new EnrollView (backend);
            stack.add_titled (enroll_view, "enroll", "Enroll");
            
            // 3. Settings view
            settings_view = new SettingsView (backend);
            stack.add_titled (settings_view, "settings", "Settings");

            // 4. Login view (Secure Startup Overlay)
            login_view = new LoginView (backend);
            login_view.authenticated.connect (() => {
                login_view.visible = false;
                stack.visible = true;
                header.sensitive = true;
            });

            overlay = new Gtk.Overlay ();
            overlay.set_child (stack);
            overlay.add_overlay (login_view);
            
            // Initial State: Locked
            stack.visible = false;
            header.sensitive = false;
            set_child (overlay);
            
            // Connect Signals for immediate UX
            enroll_view.enrollment_completed.connect (() => {
                 auth_view.refresh_users.begin ();
                 stack.set_visible_child_name ("auth");
            });

            // Apply styling
            var css_provider = new Gtk.CssProvider ();
            var css_file = File.new_for_path ("src/style.css");
            css_provider.load_from_file (css_file);

            Gtk.StyleContext.add_provider_for_display (
                                                       Gdk.Display.get_default (),
                                                       css_provider,
                                                       Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            );
        }

        private async void initialize_backend () {
            if (!yield backend.start ()) {
                var dialog = new Gtk.AlertDialog ("Failed to start backend service");
                dialog.show (this);
                return;
            }

            var result = yield backend.call_method ("initialize");

            if (result == null) {
                var dialog = new Gtk.AlertDialog ("Failed to initialize backend");
                dialog.show (this);
                return;
            }

            var result_obj = result.get_object ();
            if (!result_obj.get_boolean_member ("success")) {
                var error = result_obj.get_string_member ("error");
                var dialog = new Gtk.AlertDialog ("Initialization error: %s".printf (error));
                dialog.show (this);
            } else {
                // Backend ready
                auth_view.on_backend_ready ();
                auth_view.refresh_users.begin ();

                // First-run wizard: If no users enrolled, switch to enroll tab
                var users_res = yield backend.call_method ("get_enrolled_users");

                if (users_res != null) {
                    var u_obj = users_res.get_object ();
                    if (u_obj != null && u_obj.has_member ("success") && u_obj.get_boolean_member ("success")) {
                        var users_arr = u_obj.get_array_member ("users");
                        if (users_arr.get_length () == 0) {
                            stack.set_visible_child_name ("enroll");
                        }
                    }
                }
            }
        }

        private void on_backend_error (string error) {
            var dialog = new Gtk.AlertDialog ("Backend error: %s".printf (error));
            dialog.show (this);
        }

        public override void dispose () {
            backend.stop ();
            base.dispose ();
        }
    }
}