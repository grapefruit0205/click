/* CPython 3.12 observation companion for explicit native observer profiles.
 * This stream is necessary, never sufficient, for authoritative observation.
 * Native state is private; Python messages/Shadow JSON cannot issue evidence.
 */
#ifndef _WIN32
#define _GNU_SOURCE
#endif
#include <Python.h>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#else
#include <dlfcn.h>
#include <fcntl.h>
#endif
#include <stdlib.h>
#include <string.h>
#ifndef _WIN32
#include <unistd.h>
#endif

#ifdef _WIN32
static HANDLE channel = INVALID_HANDLE_VALUE;
#else
static int channel = -1;
#endif
static int active = 0;
static int installing = 0;
static int initialized = 0;
static unsigned long emitted = 0;
static PyCFunction clock_functions[64];
static int clock_count = 0;
static PyCFunction reporting_clock = NULL;
static PyCFunction descriptor_functions[16];
static int descriptor_count = 0;
static PyCFunction thread_functions[4];
static int thread_count = 0;
static PyCodeObject *runner_code = NULL;
static PyCodeObject *program_code = NULL;
static PyCodeObject *case_code = NULL;
static char *project_root = NULL;

static int project_prefix(const char *filename) {
    size_t length;
    if (!project_root || !filename) return 0;
    length = strlen(project_root);
    if (strlen(filename) <= length) return 0;
#ifdef _WIN32
    if (_strnicmp(filename, project_root, length) != 0) return 0;
    return filename[length] == '/' || filename[length] == '\\';
#else
    return !strncmp(filename, project_root, length) && filename[length] == '/';
#endif
}

static int project_frame(PyFrameObject *frame) {
    if (!project_root || !frame) return 0;
    PyCodeObject *code = PyFrame_GetCode(frame);
    const char *filename = code ? PyUnicode_AsUTF8(code->co_filename) : NULL;
    int match = project_prefix(filename);
    Py_XDECREF(code);
    return match;
}

static void note(unsigned int bit, const char *message) {
    unsigned long mask = 1UL << bit;
    size_t length;
#ifdef _WIN32
    DWORD written = 0;
    if (channel == INVALID_HANDLE_VALUE || (emitted & mask)) return;
#else
    if (channel < 0 || (emitted & mask)) return;
#endif
    emitted |= mask;
    length = strlen(message);
#ifdef _WIN32
    if (!WriteFile(channel, message, (DWORD)length, &written, NULL) ||
        (size_t)written != length) {
        CloseHandle(channel);
        channel = INVALID_HANDLE_VALUE;
    }
#else
    if (write(channel, message, length) != (ssize_t)length) close(channel), channel = -1;
#endif
}

static void finished(void) {
    note(1, "native-finished\n");
#ifdef _WIN32
    if (channel != INVALID_HANDLE_VALUE) CloseHandle(channel);
    channel = INVALID_HANDLE_VALUE;
#else
    if (channel >= 0) close(channel);
    channel = -1;
#endif
}

static PyCodeObject *method_code(PyObject *module, const char *cls, const char *method) {
    PyObject *type = PyObject_GetAttrString(module, cls);
    PyObject *function = type ? PyObject_GetAttrString(type, method) : NULL;
    Py_XDECREF(type);
    if (!function || !PyFunction_Check(function)) {
        Py_XDECREF(function);
        PyErr_Clear();
        return NULL;
    }
    PyCodeObject *code = (PyCodeObject *)PyFunction_GetCode(function);
    Py_XINCREF(code);
    Py_DECREF(function);
    return code;
}

static int reporting_frame(PyFrameObject *frame) {
    PyCodeObject *code = PyFrame_GetCode(frame);
    int is_case = code == case_code;
    int match = code == runner_code || is_case;
    Py_XDECREF(code);
    if (!match) return 0;
    PyFrameObject *back = PyFrame_GetBack(frame);
    if (!back) return 0;
    code = PyFrame_GetCode(back);
    match = is_case || code == program_code;
    Py_XDECREF(code);
    /* A test invoking another runner can consume its timing results. Reject
       that nested stack even if the immediate timer belongs to unittest. */
    while (back) {
        if (project_frame(back)) match = 0;
        PyFrameObject *next = PyFrame_GetBack(back);
        Py_DECREF(back);
        back = next;
    }
    return match;
}

static int profile_impl(PyObject *unused, PyFrameObject *frame, int event, PyObject *callable) {
    (void)unused;
    if (!active || installing) return 0;
    if (event == PyTrace_CALL && project_frame(frame)) {
        PyCodeObject *code = PyFrame_GetCode(frame);
        const char *sensitive[] = {"_outcome", "collectedDurations", "__dict__", "__getattribute__",
                                  "__getattr__", "__code__", "__globals__", NULL};
        for (Py_ssize_t index = 0; code && index < PyTuple_Size(code->co_names); ++index) {
            PyObject *name = PyTuple_GetItem(code->co_names, index);
            for (int entry = 0; sensitive[entry]; ++entry)
                if (PyUnicode_CompareWithASCIIString(name, sensitive[entry]) == 0)
                    note(9, "dynamic-runtime-introspection\n");
        }
        Py_XDECREF(code);
    }
    if (event != PyTrace_C_CALL || !PyCFunction_Check(callable)) return 0;
    PyCFunction pointer = PyCFunction_GetFunction(callable);
    for (int index = 0; index < clock_count; ++index) {
        if (clock_functions[index] == pointer) {
            if (pointer != reporting_clock || !reporting_frame(frame))
                note(2, "time-random-input\n");
            return 0;
        }
    }
    /* These immutable C function definitions cannot be disguised by changing
       a Python __module__ attribute. Unknown native modules are also rejected
       by the independent parent trace parser. */
    const char *name = ((PyCFunctionObject *)callable)->m_ml->ml_name;
    const char *nondeterministic[] = {
        "random", "getrandbits", "randbytes", "now", "today", "utcnow",
        "uuid1", "uuid4", "token_bytes", "token_hex", "token_urlsafe", NULL
    };
    if (project_frame(frame))
        for (int index = 0; nondeterministic[index]; ++index)
            if (!strcmp(name, nondeterministic[index]))
                note(2, "time-random-input\n");
    if (project_frame(frame) && (!strcmp(name, "getattr") || !strcmp(name, "vars") ||
        !strcmp(name, "eval") || !strcmp(name, "exec") || !strcmp(name, "compile")))
        note(9, "dynamic-runtime-introspection\n");
    if (!strcmp(name, "urandom") || !strcmp(name, "getrandom") || !strcmp(name, "times"))
        note(2, "time-random-input\n");
    if (!strcmp(name, "getprofile"))
        note(3, "observer-introspection-or-tampering\n");
    for (int index = 0; index < thread_count; ++index)
        if (thread_functions[index] == pointer)
            note(11, "concurrent-execution-unsupported\n");
    for (int index = 0; index < descriptor_count; ++index)
        if (descriptor_functions[index] == pointer)
            note(4, "descriptor-operation-needs-review\n");
    return 0;
}

static int profile(PyObject *unused, PyFrameObject *frame, int event, PyObject *callable) {
    PyObject *previous = PyErr_GetRaisedException();
    profile_impl(unused, frame, event, callable);
    if (PyErr_Occurred()) {
        note(5, "native-profile-unavailable\n");
        PyErr_Clear();
    }
    PyErr_SetRaisedException(previous);
    return 0;
}

static void install_profile(void) {
    installing = 1;
    PyObject *time_module = PyImport_ImportModule("time");
    PyObject *runner = PyImport_ImportModule("unittest.runner");
    PyObject *program = PyImport_ImportModule("unittest.main");
    PyObject *test_case = PyImport_ImportModule("unittest.case");
    PyObject *posix = PyImport_ImportModule("posix");
    PyObject *thread = PyImport_ImportModule("_thread");
    const char *descriptor_names[] = {"write", "writev", "pwrite", "dup", "dup2", "closerange", NULL};
    if (posix) {
        for (int index = 0; descriptor_names[index]; ++index) {
            PyObject *function = PyObject_GetAttrString(posix, descriptor_names[index]);
            if (function && PyCFunction_Check(function))
                descriptor_functions[descriptor_count++] = PyCFunction_GetFunction(function);
            Py_XDECREF(function);
            PyErr_Clear();
        }
    }
    Py_XDECREF(posix);
    const char *thread_names[] = {"start_new_thread", "start_new", NULL};
    if (thread) {
        for (int index = 0; thread_names[index]; ++index) {
            PyObject *function = PyObject_GetAttrString(thread, thread_names[index]);
            if (function && PyCFunction_Check(function))
                thread_functions[thread_count++] = PyCFunction_GetFunction(function);
            Py_XDECREF(function);
            PyErr_Clear();
        }
    }
    Py_XDECREF(thread);
    if (time_module && runner && program && test_case) {
        PyObject *key, *value;
        Py_ssize_t position = 0;
        PyObject *dictionary = PyModule_GetDict(time_module);
        while (PyDict_Next(dictionary, &position, &key, &value)) {
            if (!PyCFunction_Check(value)) continue;
            if (clock_count == 64) { note(5, "native-profile-unavailable\n"); break; }
            clock_functions[clock_count++] = PyCFunction_GetFunction(value);
            if (PyUnicode_Check(key) && PyUnicode_CompareWithASCIIString(key, "perf_counter") == 0)
                reporting_clock = PyCFunction_GetFunction(value);
        }
        runner_code = method_code(runner, "TextTestRunner", "run");
        program_code = method_code(program, "TestProgram", "runTests");
        case_code = method_code(test_case, "TestCase", "run");
    }
    Py_XDECREF(time_module);
    Py_XDECREF(runner);
    Py_XDECREF(program);
    Py_XDECREF(test_case);
    if (!runner_code || !program_code || !case_code || !reporting_clock ||
        !clock_count || !thread_count || PyErr_Occurred()) {
        note(5, "native-profile-unavailable\n");
        PyErr_Clear();
    } else {
        PyEval_SetProfile(profile, NULL);
        active = 1;
        note(6, "native-profile-ready\n");
    }
    installing = 0;
}

static int audit_impl(const char *event, PyObject *args, void *data) {
    (void)data;
    PyFrameObject *current = Py_IsInitialized() ? PyEval_GetFrame() : NULL;
    int project_call = project_frame(current);
    if (!strcmp(event, "cpython.run_module")) {
        if (active) {
            if (project_call) note(3, "observer-introspection-or-tampering\n");
        } else {
            PyObject *module = PyTuple_Size(args) > 0 ? PyTuple_GetItem(args, 0) : NULL;
            if (module && PyUnicode_Check(module) && PyUnicode_CompareWithASCIIString(module, "unittest") == 0)
                install_profile();
            else note(5, "native-profile-unavailable\n");
        }
    }
    if (installing) return 0;
    if (active && project_call && (!strcmp(event, "sys.setprofile") || !strcmp(event, "sys.settrace") ||
                   !strcmp(event, "sys._getframe") || !strcmp(event, "sys._current_frames")))
        note(3, "observer-introspection-or-tampering\n");
    if (active && project_call && (!strcmp(event, "code.__new__") || !strcmp(event, "function.__new__")))
        note(3, "observer-introspection-or-tampering\n");
    if (active && project_call && !strcmp(event, "object.__getattr__") && PyTuple_Size(args) > 1) {
        PyObject *attribute = PyTuple_GetItem(args, 1);
        if (PyUnicode_Check(attribute) &&
            (PyUnicode_CompareWithASCIIString(attribute, "f_code") == 0 ||
             PyUnicode_CompareWithASCIIString(attribute, "tb_frame") == 0 ||
             PyUnicode_CompareWithASCIIString(attribute, "__code__") == 0))
            note(3, "observer-introspection-or-tampering\n");
    }
    if (!strncmp(event, "subprocess.", 11) || !strncmp(event, "os.fork", 7) ||
        !strncmp(event, "os.exec", 7) || !strncmp(event, "os.spawn", 8))
        note(10, "child-process-unsupported\n");
    if (!strncmp(event, "ctypes.", 7) || !strncmp(event, "socket.", 7) ||
        !strncmp(event, "sqlite3.", 8))
        note(7, "external-or-native-input\n");
    if (active && project_call && !strcmp(event, "import") && PyTuple_Size(args) > 1) {
        PyObject *filename = PyTuple_GetItem(args, 1);
        if (filename && PyUnicode_Check(filename)) {
            const char *path = PyUnicode_AsUTF8(filename);
            size_t length = path ? strlen(path) : 0;
            if ((length > 3 && !strcmp(path + length - 3, ".so")) ||
                (length > 4 && !strcmp(path + length - 4, ".pyd")) ||
                (length > 6 && !strcmp(path + length - 6, ".dylib")))
                note(7, "external-or-native-input\n");
        }
    }
    if (active && !strcmp(event, "open") && PyTuple_Size(args) > 0) {
        PyObject *path = PyTuple_GetItem(args, 0);
        if (PyLong_Check(path)) note(8, "inherited-descriptor-input\n");
    }
    return 0;
}

static int audit(const char *event, PyObject *args, void *data) {
    if (!Py_IsInitialized()) return audit_impl(event, args, data);
    PyObject *previous = PyErr_GetRaisedException();
    audit_impl(event, args, data);
    if (PyErr_Occurred()) {
        note(5, "native-profile-unavailable\n");
        PyErr_Clear();
    }
    PyErr_SetRaisedException(previous);
    return 0;
}

static void initialize(void) {
    /* No Python API is used until confirming this is the selected interpreter.
       strace itself runs without this preload in the parent implementation. */
    typedef int (*add_hook)(Py_AuditHookFunction, void *);
#ifdef _WIN32
    add_hook register_hook = PySys_AddAuditHook;
    const char *handle_text = getenv("CLICK_NATIVE_OBSERVER_HANDLE");
    const char *root = getenv("CLICK_NATIVE_OBSERVER_ROOT");
    unsigned long long inherited = 0;
    if (initialized) return;
    initialized = 1;
    if (!handle_text || !handle_text[0]) return;
    inherited = _strtoui64(handle_text, NULL, 10);
    if (!inherited) return;
    channel = (HANDLE)(uintptr_t)inherited;
    _putenv_s("CLICK_NATIVE_OBSERVER_HANDLE", "");
    _putenv_s("CLICK_NATIVE_OBSERVER_ROOT", "");
#else
    add_hook register_hook = (add_hook)dlsym(RTLD_DEFAULT, "PySys_AddAuditHook");
    const char *path = getenv("CLICK_NATIVE_OBSERVER_CHANNEL");
    const char *root = getenv("CLICK_NATIVE_OBSERVER_ROOT");
    if (initialized) return;
    initialized = 1;
    if (!register_hook || !path || path[0] != '/') return;
    if (root && root[0] == '/') project_root = strdup(root);
    channel = open(path, O_WRONLY | O_CLOEXEC | O_NOFOLLOW);
    unsetenv("CLICK_NATIVE_OBSERVER_CHANNEL");
    unsetenv("CLICK_NATIVE_OBSERVER_ROOT");
    unsetenv("LD_PRELOAD");
    unsetenv("DYLD_INSERT_LIBRARIES");
    unsetenv("DYLD_FORCE_FLAT_NAMESPACE");
    if (channel < 0) return;
#endif
#ifdef _WIN32
    if (root && root[0]) project_root = _strdup(root);
    if (channel == INVALID_HANDLE_VALUE) return;
#endif
    note(0, "native-started\n");
    if (!project_root) note(5, "native-profile-unavailable\n");
    if (register_hook(audit, NULL) != 0) note(5, "native-profile-unavailable\n");
    atexit(finished);
}

#ifndef _WIN32
__attribute__((constructor)) static void preload_initialize(void) {
    initialize();
}
#endif

static PyObject *mark_startup_unsafe(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    note(7, "external-or-native-input\n");
    Py_RETURN_NONE;
}

static PyMethodDef companion_methods[] = {
    {"mark_startup_unsafe", mark_startup_unsafe, METH_NOARGS, NULL},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef companion_module = {
    PyModuleDef_HEAD_INIT,
    "_click_observer_companion",
    NULL,
    -1,
    companion_methods,
    NULL,
    NULL,
    NULL,
    NULL
};

PyMODINIT_FUNC PyInit__click_observer_companion(void) {
    initialize();
    return PyModule_Create(&companion_module);
}
