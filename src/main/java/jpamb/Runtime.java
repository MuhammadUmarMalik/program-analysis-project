package jpamb;

import java.lang.reflect.*;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.regex.*;
import java.util.stream.Stream;

import jpamb.utils.*;
import jpamb.utils.CaseContent.ResultType;
import jpamb.cases.*;

/**
 * The runtime method runs a single test-case and prints the result or exception.
 */
public class Runtime {
  static List<Class<?>> caseclasses = List.of(
      Simple.class,
      Loops.class,
      Tricky.class,
      jpamb.cases.Arrays.class,
      Calls.class,
      Strings.class);

  public static Case[] cases(Method m) {
    var cases = m.getAnnotation(Cases.class);
    if (cases == null) {
      var c = m.getAnnotation(Case.class);
      if (c == null)
        return new Case[] {};
      return new Case[] { c };
    } else {
      return cases.value();
    }
  }

  public static void printType(Class<?> c, StringBuilder b) {
    if (c.equals(void.class)) {
      b.append("V");
    } else if (c.equals(int.class)) {
      b.append("I");
    } else if (c.equals(boolean.class)) {
      b.append("Z");
    } else if (c.equals(double.class)) {
      b.append("D");
    } else if (c.equals(float.class)) {
      b.append("F");
    } else if (c.equals(char.class)) {
      b.append("C");
    } else if (c.equals(int[].class)) {
      b.append("[I");
    } else if (c.equals(char[].class)) {
      b.append("[C");
    } else if (c.equals(java.lang.String.class)) {
      b.append("L");
    } else {
      throw new RuntimeException("Unknown type:" + c.toString());
    }
  }

  public static String printMethodSignature(Method m) {
    StringBuilder b = new StringBuilder();
    b.append("(");
    for (Class<?> c : m.getParameterTypes()) {
      printType(c, b);
    }
    b.append(")");
    printType(m.getReturnType(), b);
    return b.toString();
  }

  public static Class<?>[] parseMethodSignature(String s) {
    List<Class<?>> params = new ArrayList<>();
    for (int i = 0; i < s.length(); i++) {
        char c = s.charAt(i);
        switch (c) {
            case 'I' -> params.add(int.class);
            case 'Z' -> params.add(boolean.class);
            case 'C' -> params.add(char.class);
            case 'L' -> {
                // Skip until ';' – it's an object type (e.g., Ljava/lang/String;)
                int semi = s.indexOf(';', i);
                String typeName = s.substring(i + 1, semi).replace('/', '.');
                if (typeName.equals("java.lang.String")) {
                    params.add(String.class);
                } else {
                    try {
                        params.add(Class.forName(typeName));
                    } catch (ClassNotFoundException e) {
                        throw new RuntimeException("Unknown type in descriptor: " + typeName, e);
                    }
                }
                i = semi; // jump past ';'
            }
            case '[' -> {
                // Simple handling for primitive arrays
                i++;
                if (i < s.length()) {
                    switch (s.charAt(i)) {
                        case 'I' -> params.add(int[].class);
                        case 'Z' -> params.add(boolean[].class);
                        case 'C' -> params.add(char[].class);
                    }
                }
            }
        }
    }
    return params.toArray(new Class<?>[0]);
  }

  /** Converts a JVM-style descriptor (e.g., Ljpamb/cases/Arrays;) into a Java name. */
  public static String normalizeClassName(String cls) {
    if (cls.startsWith("L") && cls.endsWith(";")) {
        cls = cls.substring(1, cls.length() - 1);
    }
    cls = cls.replace('/', '.');
    // 👇 Add this line:
    if (!cls.contains(".")) {
        cls = "jpamb.cases." + cls;
    }
    return cls;
}


  public static void main(String[] args)
      throws ClassNotFoundException, NoSuchMethodException, IllegalAccessException {
    if (args.length == 0) {
      var mths = caseclasses.stream().flatMap(c -> Stream.of(c.getMethods())).toList();
      for (Method m : mths) {
        for (Case c : cases(m)) {
          CaseContent content = CaseContent.parse(c.value());
          String sig = printMethodSignature(m);
          String id = m.getDeclaringClass().getName() + "." + m.getName() + ":" + sig;
          if (!Modifier.isStatic(m.getModifiers())) {
            throw new RuntimeException("Expected " + id + " to be static");
          }
          System.out.printf("%-60s %s%n", id, content);
        }
      }
      return;
    }

    String thecase = args[0];
    Pattern pattern = Pattern.compile("(.*)\\.([^.(]*):\\((.*)\\)(.*)");
    Matcher matcher = pattern.matcher(thecase);
    if (matcher.find()) {
      String cls = normalizeClassName(matcher.group(1));
      String mth = matcher.group(2);
      String prams = matcher.group(3);

      Method m = Class.forName(cls).getMethod(mth, parseMethodSignature(prams));

      if (!Modifier.isStatic(m.getModifiers())) {
        throw new RuntimeException("Expected " + pattern + " to be static");
      }
      for (int i = 1; i < args.length; i++) {
      String raw = args[i];                           // ← keep raw input
      Object[] params = InputParser.parse(raw);
      System.err.printf("Running %s with %s%n", m, Arrays.toString(params));

      // ---- array coercions / fallbacks ----
      Object[] invokeParams = params;
      Class<?>[] expected = m.getParameterTypes();

      if (expected.length == 1) {
        Class<?> exp = expected[0];

        // int[]
        if (exp.equals(int[].class)) {
          // Case A: already got an int[] — pass through
          if (params.length == 1 && params[0] instanceof int[]) {
            invokeParams = new Object[] { (int[]) params[0] };
          }
          // Case B: got N>=1 Integers — pack them into one int[]
          else if (params.length >= 1) {
            boolean allInt = true;
            for (Object p : params) {
              if (!(p instanceof Integer)) { allInt = false; break; }
            }
            if (allInt) {
              int[] arr = new int[params.length];
              for (int k = 0; k < params.length; k++) arr[k] = (Integer) params[k];
              invokeParams = new Object[] { arr };
            }
          }
          // Case C: got 0 args — build from raw or pass empty int[]
          else if (params.length == 0) {
            int[] arr = new int[0];
            String rin = raw == null ? "" : raw.trim();
            Matcher mArr = Pattern
                .compile("array\\s+int\\s*\\(([^)]*)\\)", Pattern.CASE_INSENSITIVE)
                .matcher(rin);
            if (mArr.find()) {
              String inside = mArr.group(1).trim();
              if (!inside.isEmpty()) {
                String[] toks = inside.split("[,\\s]+");
                List<Integer> vals = new ArrayList<>();
                for (String t : toks) if (!t.isEmpty()) vals.add(Integer.parseInt(t));
                arr = new int[vals.size()];
                for (int k = 0; k < vals.size(); k++) arr[k] = vals.get(k);
              }
            }
            invokeParams = new Object[] { arr };
          }
        }

        else if (exp.equals(char[].class)) {
          if (params.length == 1 && params[0] instanceof Character) {
            invokeParams = new Object[] { new char[] { (Character) params[0] } };
          } else if (params.length == 0) {
            char[] arr = new char[0];
            String rin = raw == null ? "" : raw.trim();
            java.util.regex.Matcher mArr = java.util.regex.Pattern
              .compile("array\\s+char\\s*\\(([^)]*)\\)", java.util.regex.Pattern.CASE_INSENSITIVE)
              .matcher(rin);
            if (mArr.find()) {
              String inside = mArr.group(1).trim();
              if (!inside.isEmpty()) {
                inside = inside.replace("'", "").replace("\"", "");
                String[] toks = inside.split("[,\\s]+");
                java.util.List<Character> vals = new java.util.ArrayList<>();
                for (String t : toks) if (!t.isEmpty()) vals.add(t.charAt(0));
                arr = new char[vals.size()];
                for (int k = 0; k < vals.size(); k++) arr[k] = vals.get(k);
              }
            }
            invokeParams = new Object[] { arr };
          }
        }

        else if (exp.equals(boolean.class)) {
          // Case A: already parsed a Boolean
          if (params.length == 1 && params[0] instanceof Boolean) {
            // nothing to do
          }
          // Case B: no params -> parse from raw like "(false)", "false", or "(bool false)"
          else if (params.length == 0) {
            String rin = raw == null ? "" : raw.trim();
            // strip outer parens if present
            if (rin.startsWith("(") && rin.endsWith(")")) {
              rin = rin.substring(1, rin.length() - 1).trim();
            }
            // optional "bool"/"boolean" prefix
            rin = rin.replaceFirst("(?i)^(bool|boolean)\\s+", "").trim();
            if ("true".equalsIgnoreCase(rin) || "false".equalsIgnoreCase(rin)) {
              invokeParams = new Object[] { Boolean.parseBoolean(rin.toLowerCase()) };
            }
          }
          // Case C: weird multiple tokens (defensive) -> pick first Boolean if any
          else if (params.length > 1) {
            for (Object p : params) {
              if (p instanceof Boolean) {
                invokeParams = new Object[] { p };
                break;
              }
            }
          }
        }

      }

      try {
        m.invoke(null, invokeParams);

        } catch (InvocationTargetException e) {
          System.out.println(ResultType.fromThrowable(e.getCause()));
          return;
        }
      }
      System.out.println(ResultType.SUCCESS);
    }
  }
}
