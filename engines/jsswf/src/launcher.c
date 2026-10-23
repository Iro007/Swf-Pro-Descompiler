#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

#include "bundle_hash.h"

static const char * embedded_bundle_hash = JSSWF_BUNDLE_HASH;

int main(int argc, char **argv) {
    (void)embedded_bundle_hash;
    char exe_path[MAX_PATH];
    DWORD len = GetModuleFileNameA(NULL, exe_path, MAX_PATH);
    if (len == 0 || len >= MAX_PATH) {
        fprintf(stderr, "Failed to resolve jsswf.exe path\n");
        return 1;
    }
    /* Strip \dist\jsswf.exe -> Js_swf root */
    char *last_sep = strrchr(exe_path, '\\');
    if (last_sep) *last_sep = '\0';
    char *parent_sep = strrchr(exe_path, '\\');
    if (parent_sep) *parent_sep = '\0';

    char cli_mjs[MAX_PATH * 2];
    snprintf(cli_mjs, sizeof(cli_mjs), "%s\\src\\cli.mjs", exe_path);

    const char *node_exe = "C:\\Program Files\\nodejs\\node.exe";
    DWORD attr = GetFileAttributesA(node_exe);
    if (attr == INVALID_FILE_ATTRIBUTES) {
        node_exe = "node.exe";
    }

    char cmdline[8192];
    snprintf(cmdline, sizeof(cmdline), "\"%s\" \"%s\"", node_exe, cli_mjs);
    for (int i = 1; i < argc; i++) {
        strncat(cmdline, " \"", sizeof(cmdline) - strlen(cmdline) - 1);
        strncat(cmdline, argv[i], sizeof(cmdline) - strlen(cmdline) - 1);
        strncat(cmdline, "\"", sizeof(cmdline) - strlen(cmdline) - 1);
    }

    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    si.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    si.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    ZeroMemory(&pi, sizeof(pi));

    HANDLE job = CreateJobObjectA(NULL, NULL);
    if (job) {
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION jeli;
        ZeroMemory(&jeli, sizeof(jeli));
        jeli.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK;
        SetInformationJobObject(job, JobObjectExtendedLimitInformation, &jeli, sizeof(jeli));
    }

    if (!CreateProcessA(NULL, cmdline, NULL, NULL, TRUE, 0, NULL, NULL, &si, &pi)) {
        if (job) CloseHandle(job);
        fprintf(stderr, "Failed to launch node.exe for JsSWF\n");
        return 1;
    }
    if (job) {
        AssignProcessToJobObject(job, pi.hProcess);
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD exit_code = 1;
    GetExitCodeProcess(pi.hProcess, &exit_code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    if (job) CloseHandle(job);
    return (int)exit_code;
}
