/* pam_sentinel.c - Connects Linux Auth to Sentinel Daemon */
#include <stdio.h>

#include <security/pam_ext.h>
#include <security/pam_modules.h>
#include <string.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <poll.h>
#include <errno.h>
#include <time.h>

#include <syslog.h>

#define SOCKET_PATH "/run/sentinel/sentinel.sock"

/* Send request to Python and get response in a streaming loop.
 * Renders intermediate instructions natively via PAM.
 * NOTE: Caller must have called openlog() before this function.
 * syslog is open for the duration of pam_sm_authenticate.
 */
int check_face_auth(pam_handle_t *pamh, const char *username) {
  int sock = 0;
  struct sockaddr_un serv_addr;
  char buffer[4096] = {0};
  char request[512];

  // 1. Create Socket
  if ((sock = socket(AF_UNIX, SOCK_STREAM, 0)) < 0) {
    syslog(LOG_ERR, "Failed to create AF_UNIX socket");
    return 0;
  }

  memset(&serv_addr, 0, sizeof(serv_addr));
  serv_addr.sun_family = AF_UNIX;
  strncpy(serv_addr.sun_path, SOCKET_PATH, sizeof(serv_addr.sun_path) - 1);

  // 2. Connect with 15s timeout (model warmup + auth loop can take 10-15s)
  struct timeval timeout;
  timeout.tv_sec = 15;
  timeout.tv_usec = 0;
  setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));

  if (connect(sock, (struct sockaddr *)&serv_addr, sizeof(serv_addr)) < 0) {
    syslog(LOG_ERR, "Failed to connect to %s (Daemon not running?)", SOCKET_PATH);
    close(sock);
    return 0;
  }
  syslog(LOG_INFO, "Connected to Daemon for user %s", username ? username : "unknown");

  // 3. Send JSON-RPC request (ID=99 reserved for PAM)
  if (username) {
    snprintf(request, sizeof(request),
             "{\"method\": \"authenticate_pam\", \"params\": {\"user\": "
             "\"%s\"}, \"id\": 99}\n",
             username);
  } else {
    snprintf(request, sizeof(request),
             "{\"method\": \"authenticate_pam\", \"params\": {}, \"id\": 99}\n");
  }

  if (send(sock, request, strlen(request), 0) < 0) {
    syslog(LOG_ERR, "Failed to send JSON-RPC request to daemon");
    close(sock);
    return 0;
  }
  syslog(LOG_INFO, "Sent JSON-RPC request, waiting for response (up to 15s)...");

  // 4. Read response stream (up to 15s total timeout)
  ssize_t total = 0;
  int auth_success = 0;
  char *line_start = buffer;
  
  struct pollfd pfd;
  pfd.fd = sock;
  pfd.events = POLLIN;
  
  time_t start_loop = time(NULL);

  while (time(NULL) - start_loop < 15) {
    // 4a. Check if room in buffer
    if (total >= sizeof(buffer) - 1) {
       size_t len = buffer + total - line_start;
       memmove(buffer, line_start, len);
       total = len;
       line_start = buffer;
    }
    
    // 4b. Poll for data (500ms timeout)
    int pr = poll(&pfd, 1, 500);
    if (pr < 0) {
       if (errno == EINTR) continue;
       syslog(LOG_ERR, "Poll error: %s", strerror(errno));
       break;
    }
    if (pr == 0) continue; // Timeout, check loop time
    
    // 4c. Read data
    ssize_t valread = read(sock, buffer + total, sizeof(buffer) - 1 - total);
    if (valread < 0) {
       if (errno == EAGAIN || errno == EINTR) continue;
       break;
    }
    if (valread == 0) break; // Socket closed
    
    total += valread;
    buffer[total] = '\0';
    
    // 4d. Process all complete lines
    while (1) {
       char *newline = strchr(line_start, '\n');
       if (!newline) break;
       
       *newline = '\0';
       syslog(LOG_INFO, "PAM Read: %s", line_start);
       
       if (strstr(line_start, "\"method\": \"pam_info\"")) {
           char *text_start = strstr(line_start, "\"text\": \"");
           if (text_start) {
               text_start += 9;
               char *text_end = strchr(text_start, '\"');
               if (text_end) {
                   *text_end = '\0';
                   syslog(LOG_INFO, "PAM: Displaying pam_info: '%s'", text_start);
                   // Use pam_error for visibility in terminal (pam_info may be suppressed)
                   pam_error(pamh, "%s", text_start);
               }
           }
       }
       if (strstr(line_start, "\"result\": \"FAILED\"")) {
           char *err_start = strstr(line_start, "\"error\": \"");
           if (err_start) {
               err_start += 10;
               char *err_end = strchr(err_start, '\"');
               if (err_end) {
                   *err_end = '\0';
                   pam_error(pamh, "Sentinel Error: %s", err_start);
               }
           } else {
               pam_error(pamh, "Sentinel: Face Recognition Failed.");
           }
           total = 0; // Terminate early
           break;
       }
       else if (strstr(line_start, "\"result\"")) {
           if (strstr(line_start, "\"SUCCESS\"")) {
               auth_success = 1;
           }
           goto FINISH_STREAM;
       }
       
       line_start = newline + 1;
    }
  }

FINISH_STREAM:
  close(sock);
  syslog(LOG_INFO, "PAM Auth finished, success=%d", auth_success);
  return auth_success;
}

PAM_EXTERN int pam_sm_authenticate(pam_handle_t *pamh, int flags, int argc,
                                   const char **argv) {
  const char *user = NULL;
  int retval;
  int auth_result;

  // Open syslog ONCE for the whole authentication call
  openlog("pam_sentinel", LOG_PID | LOG_CONS, LOG_AUTH);

  // Get the username
  retval = pam_get_user(pamh, &user, NULL);
  if (retval != PAM_SUCCESS) {
    syslog(LOG_ERR, "pam_get_user failed: %d", retval);
    closelog();
    return retval;
  }
  syslog(LOG_INFO, "PAM module initialized for user: %s", user ? user : "unknown");

  // Check if we should skip for some users (optional, maybe via PAM args?)
  // if (strcmp(user, "root") == 0) return PAM_IGNORE;

  // Call our Python Backend — returns 1 on face match, 0 on fail/timeout
  auth_result = check_face_auth(pamh, user);

  // Close syslog ONCE — paired with the openlog at the top of this function
  closelog();

  if (auth_result) {
    return PAM_SUCCESS;
  }
  return PAM_AUTH_ERR;  // Fallback to password prompt
}

PAM_EXTERN int pam_sm_setcred(pam_handle_t *pamh, int flags, int argc,
                              const char **argv) {
  return PAM_SUCCESS;
}

PAM_EXTERN int pam_sm_acct_mgmt(pam_handle_t *pamh, int flags, int argc,
                                const char **argv) {
  return PAM_SUCCESS;
}

PAM_EXTERN int pam_sm_open_session(pam_handle_t *pamh, int flags, int argc,
                                   const char **argv) {
  return PAM_SUCCESS;
}

PAM_EXTERN int pam_sm_close_session(pam_handle_t *pamh, int flags, int argc,
                                    const char **argv) {
  return PAM_SUCCESS;
}

PAM_EXTERN int pam_sm_chauthtok(pam_handle_t *pamh, int flags, int argc,
                                const char **argv) {
  return PAM_SUCCESS;
}
