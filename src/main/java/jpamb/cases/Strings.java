package jpamb.cases;

import jpamb.utils.*;
import static jpamb.utils.Tag.TagType.*;

public class Strings {

  @Case("() -> out of bounds")
  @Tag({ STRING }) // or ARRAY if you don't have STRING
  public static void stringOutOfBounds() {
    String s = "hi";
    // length = 2, valid indices: 0,1
    s.charAt(3);          // -> StringIndexOutOfBoundsException
  }

  @Case("() -> ok")
  @Tag({ STRING })
  public static void stringInBounds() {
    String s = "hi";
    char c = s.charAt(1); // ok
    assert c == 'i';
  }

  @Case("() -> ok")
  @Tag({ STRING })
  public static void stringLength() {
    String s = "hi";
    assert s.length() == 2;
  }

  @Case("() -> null pointer")
  @Tag({ STRING })
  public static void stringIsNullCharAt() {
    String s = null;
    // dereference on null -> NullPointerException
    s.charAt(1);
  }

  @Case("() -> null pointer")
  @Tag({ STRING })
  public static void stringIsNullLength() {
    String s = null;
    // dereference on null -> NullPointerException
    assert s.length() == 0;
  }

  @Case("(11) -> null pointer")
  @Case("(0) -> out of bounds")
  @Tag({ STRING })
  public static void stringSometimesNull(int i) {
    String s = null;
    if (i < 10) {
      s = String.valueOf(i); // "0".."9", length = 1
    }
    // if i >= 10 -> s is null -> NullPointerException
    // if i < 10  -> s has length 1 -> out-of-bounds on charAt(1)
    s.charAt(1);
  }

  @Case("() -> assertion error")
  @Tag({ STRING })
  public static void stringContent() {
    String s = "1 2 100 -13 23";
    // for all characters, assert they are digits (which is false for spaces / '-')
    for (int i = 0; i < s.length(); i++) {
      char c = s.charAt(i);
      assert Character.isDigit(c);  // will fail
    }
  }
 
  @Case("(\"hello\") -> ok")
  @Case("(\"world\") -> assertion error")
  @Case("(\"\") -> out of bounds")
  @Tag({ STRING })
  public static void stringSpellsHello(String s) {
    // empty string -> charAt(0) -> out of bounds
    assert s.charAt(0) == 'h'
        && s.charAt(1) == 'e'
        && s.charAt(2) == 'l'
        && s.charAt(3) == 'l'
        && s.charAt(4) == 'o';
  }

  @Case("(\"hello\") -> ok")
  @Case("(\"hi\") -> assertion error")
  @Tag({ STRING })
  public static void stringEqualsHello(String s) {
    // “pure assertion” version that never throws out-of-bounds
    assert "hello".equals(s);
  }

  @Case("(\"Hello World\") -> ok")
  @Tag({ STRING })
  public static void stringTricky(String s) {

    // 1. Using substring()
    String sub = s.substring(0, 5); // "Hello"

    // 2. Using valueOf()
    int number = 42;
    String numberAsString = String.valueOf(number); // "42"
   
    String result = sub + " number is " + numberAsString + "!";

    assert result.equals("Hello number is 42!");
  }

  /* 
  @Case("([Ljava.lang.String;: [\"50\", \"100\", \"200\"]) -> ok")
  @Case("([Ljava.lang.String;: []) -> assertion error")
  @Tag({ STRING })
  public static void stringArraySumIsLarge(String[] array) {
    int sum = 0;
    for (int i = 0; i < array.length; i++) {
      sum += Integer.parseInt(array[i]); // may throw NumberFormatException if not numeric
    }
    assert sum > 300;
  }

  @Case("([Ljava.lang.String;: []) -> assertion error")
  @Case("([Ljava.lang.String;: [\"x\"]) -> ok")
  @Tag({ STRING })
  public static void stringArrayNotEmpty(String[] array) {
    assert array.length > 0;
  }
    

  @Case("(\"c\") -> ok")
  @Case("(\"z\") -> assertion error")
  @Tag({ STRING })
  public static void stringBinarySearch(String x) {
    String[] arr = { "a", "b", "c", "k", "z" };
    int l = 0, r = arr.length - 1;
    while (l <= r) {
      int m = l + (r - l) / 2;
      int cmp = arr[m].compareTo(x);
      if (cmp == 0)
        return;
      if (cmp < 0)
        l = m + 1;
      else
        r = m - 1;
    }
    assert false;   // not found
  }
  */
}
